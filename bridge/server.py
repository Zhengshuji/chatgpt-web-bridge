#!/usr/bin/env python3
"""gpt-bridge — 把沙盒里已登录的 ChatGPT 网页反代成 OpenAI 格式 API。

链路: code CLI -> /v1/chat/completions -> CDP(9225) 驱动真实 chatgpt.com 页面 -> 免费账号
原理: 不碰 backend-api 协议(PoW/风控/Cloudflare 全都由真实网页自己处理),
      在页面里模拟输入 + 抓取流式 DOM 回复, 映射成 OpenAI SSE/JSON。
限制: 单并发(整个账号一次一个对话); 每次请求在账号里留下一条新会话记录。
"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
import websockets

# 本机化改造(相对上游 laoniubia/gpt-web-bridge):
#   GPTWEB_CDP   已启动且已登录 chatgpt.com 的 Chrome 的 CDP 地址
#   GPTWEB_PORT  本地 OpenAI 兼容端口
#   GPTWEB_HOME  会话/密钥/日志目录(默认与本文件同级的 data/)
CDP_HTTP = os.environ.get("GPTWEB_CDP", "http://127.0.0.1:9225").rstrip("/")
PORT = int(os.environ.get("GPTWEB_PORT", "8355"))
HOME = Path(__file__).resolve().parent
GW = Path(os.environ.get("GPTWEB_HOME") or (HOME / "data"))
GW.mkdir(parents=True, exist_ok=True)
# 场景提示词里告诉模型"项目代码在哪": 设了它就写进前半部分, 模型可以自己去读仓库
PROMPT_REPO = os.environ.get("GPTWEB_REPO", "").strip()
# 默认是否打开网页上的"思考"开关; 请求头 X-Thinking: 1/0 可以单次覆盖
THINKING = os.environ.get("GPTWEB_THINKING", "0").strip().lower() in ("1", "true", "yes", "on")
# 本地已经定死的规则(仓库名/分支/是否推送/不许问的东西), 直接写进提示词开头
POLICY = os.environ.get("GPTWEB_POLICY", "").strip()
KEY_FILE = GW / "key.txt"
LOG_FILE = GW / "bridge.log"

if KEY_FILE.exists():
    KEY = KEY_FILE.read_text().strip()
else:
    KEY = "sk-gptweb-" + secrets.token_hex(12)
    KEY_FILE.write_text(KEY + "\n")

CHAT_URL = "https://chatgpt.com/"
# 2026-11 版 chatgpt.com 的 DOM(中文界面)实测:
#   输入框   #prompt-textarea (ProseMirror)
#   发送键   button[aria-label="发送"]   —— 已无 data-testid="send-button"
#   停止键   button[aria-label="停止"]   —— 生成中出现
#   助手消息 [data-dil-response](内含 [data-dil-message-id])
#   用户消息 不带上述属性, 因此按助手消息计数即可判定"新增了一条回复"
COMPOSER = 'document.querySelector(\'#prompt-textarea, div[contenteditable="true"][role="textbox"]\')'
SEND_BTN = (
    'document.querySelector(\'[data-testid="send-button"], button[aria-label="发送"],'
    ' button[aria-label*="Send"]\')'
)
STOP_BTN = (
    'document.querySelector(\'[data-testid="stop-button"], button[aria-label="停止"],'
    ' button[aria-label*="Stop"]\')'
)
ASSISTANT = '[data-dil-response], [data-message-author-role="assistant"]'
SNAP_JS_TPL = (
    "(() => { const m = document.querySelectorAll('" + ASSISTANT + "');"
    " const last = m[m.length-1];"
    " const errText = last ? /maximum length|message you submitted was too long/i.test(last.innerText) : false;"
    " return JSON.stringify({ text: last ? last.innerText : '',"
    " gen: !!" + STOP_BTN + ", count: m.length,"
    " full: m.length > __BASE__ && errText }); })()"
)


async def snapshot(base_count: int = -1) -> dict:
    """base_count: 发送前记录的 assistant 消息数; full 旗标只认本轮新增消息里的错误"""
    js = SNAP_JS_TPL.replace("__BASE__", str(base_count))
    return await PAGE.eval(js)
READY_JS = (
    "(() => { return JSON.stringify({ ready: document.readyState, "
    "has: !!" + COMPOSER + " }); })()"
)

# 账号模型列表: 运行时经页面 fetch /backend-api/models 动态获取, 失败时用这份兜底
# MODEL_EXCLUDE: 用户指定剔除的模型(2026-08-30): 5.5 全系/5.3-mini/5.4-t-mini/深研模式
MODEL_EXCLUDE = {"gpt-5-5", "gpt-5-5-mini", "gpt-5-3-mini", "gpt-5-4-t-mini",
                 "research"}
MODEL_FALLBACK = ["auto", "gpt-5-6", "gpt-5-6-mini",
                  "gpt-5-6-t-mini", "gpt-5-6-t-mini-mini"]
_models_cache: list[str] = []
_models_ts: float = 0.0

BUSY = asyncio.Lock()  # 单账号同时只能聊一个窗口


def log(msg: str) -> None:
    line = f"[{time.strftime('%F %T')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------- CDP 层 ----------------

class Page:
    def __init__(self):
        self.ws = None
        self._id = 0

    async def _target(self) -> str:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{CDP_HTTP}/json/list") as r:
                tabs = await r.json()
            for t in tabs:
                if t.get("type") == "page" and "chatgpt.com" in t.get("url", ""):
                    return t["webSocketDebuggerUrl"]
            async with s.put(f"{CDP_HTTP}/json/new?{CHAT_URL}") as r:
                t = await r.json()
        return t["webSocketDebuggerUrl"]

    async def connect(self) -> None:
        url = await self._target()
        self.ws = await websockets.connect(url, max_size=50 * 1024 * 1024)

    async def ensure(self) -> None:
        if self.ws is None:
            try:
                await self.connect()
            except Exception:
                await asyncio.sleep(2)
                await self.connect()

    async def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        await self.ensure()
        self._id += 1
        i = self._id
        await self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"CDP {method} 超时")
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), left))
            if msg.get("id") == i:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr: str, timeout: float = 30) -> dict:
        r = await self.call("Runtime.evaluate",
                            {"expression": expr, "awaitPromise": True,
                             "returnByValue": True}, timeout)
        v = r.get("result", {})
        if v.get("subtype") == "error":
            raise RuntimeError(v.get("description", "JS error"))
        raw = v.get("value")
        if raw is None:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise RuntimeError(f"非 JSON 返回: {str(raw)[:80]!r}")

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None


PAGE = Page()


# ---------------- 增量会话(delta)基础设施 ----------------

class ConvoFull(Exception):
    """网页会话到达长度上限"""


SESSIONS: dict[str, dict] = {}
SESSION_KEEP = 6
# 一个网页对话最多接多少轮; 超过就主动换新对话, 免得撞上"对话过长"再补救
SESSION_ROUNDS = int(os.environ.get("GPTWEB_SESSION_ROUNDS", "20"))


def _msg_fp(m: dict) -> str:
    c = m.get("content", "")
    if isinstance(c, list):
        c = "".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    tc = json.dumps(m["tool_calls"], ensure_ascii=False) if m.get("tool_calls") else ""
    raw = f"{m.get('role', '')}|{c}|{tc}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _session_key(model_slug: str, messages: list, tools: list) -> str:
    sys_fp = _msg_fp(messages[0]) if messages else "nosys"
    tnames = ",".join(t.get("function", {}).get("name", "") for t in tools)
    raw = f"{model_slug}|{sys_fp}|{hashlib.sha1(tnames.encode()).hexdigest()[:12]}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _find_session(key: str, chain: list):
    """找指纹链是本次请求前缀的最长会话: 返回 (session, 已发送消息数)"""
    best, start = None, 0
    for s in SESSIONS.values():
        if s["key"] != key:
            continue
        if s.get("rounds", 0) >= SESSION_ROUNDS:
            continue
        fps = s["fps"]
        if len(chain) > len(fps) and chain[:len(fps)] == fps:
            if best is None or len(fps) > len(best["fps"]):
                best, start = s, len(fps)
    return best, start


def _save_session(key: str, chain: list, url: str) -> str:
    sid = key + "-" + uuid.uuid4().hex[:6]
    SESSIONS[sid] = {"sid": sid, "key": key, "fps": list(chain),
                     "url": url, "ts": time.monotonic()}
    if len(SESSIONS) > SESSION_KEEP:
        old = min(SESSIONS.values(), key=lambda s: s["ts"])
        SESSIONS.pop(old["sid"], None)
    return sid


def _remember(sess: dict | None, key: str, chain: list, url: str) -> None:
    if sess:
        sess["fps"], sess["url"], sess["ts"] = list(chain), url or sess["url"], time.monotonic()
        sess["rounds"] = sess.get("rounds", 0) + 1
    elif url:
        _save_session(key, chain, url)


def _log_request(body: dict, status: str, elapsed: float) -> None:
    """每次请求追加一行 JSONL, 出问题能回溯: 谁、多大、多久、结果。"""
    try:
        msgs = body.get("messages") or []
        last = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                c = m.get("content")
                last = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)[:300]
                break
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": body.get("model"),
            "stream": bool(body.get("stream")),
            "tools": len(body.get("tools") or []),
            "msgs": len(msgs),
            "chars": sum(len(str(m.get("content") or "")) for m in msgs),
            "elapsed_s": round(elapsed, 1),
            "status": status,
            "last_user": last[:300],
        }
        with open(GW / "requests.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 记日志失败不能影响主流程
        pass


async def _current_url() -> str:
    try:
        r = await PAGE.eval("JSON.stringify({u: location.href})")
        return r.get("u", "")
    except Exception:
        return ""


# ---------------- 网页操作 ----------------

def _last_user_text(body: dict) -> str:
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
    return ""


def _fast_answer(body: dict) -> tuple[str, str] | None:
    """能在本地直接答的请求就别去驱动网页: 既浪费时间又占着单并发锁。

    目前两类:
      - 会话标题生成(DSH 每开一个新会话问一次, 602 字符却要 40s)
      - Mnemon 的空闲审查子代理(4 个工具、15K 系统提示, 每次 30~60s, 通常无事可做)
    """
    msgs = body.get("messages") or []
    last = _last_user_text(body)
    if os.environ.get("GPTWEB_FASTPATH", "1").strip().lower() in ("0", "false", "no", "off"):
        return None

    if "Generate the session title" in last[:300]:
        title = "会话"
        try:
            i = last.find("[")
            j = last.rfind("]")
            arr = json.loads(last[i:j + 1])
            if isinstance(arr, list) and arr:
                t = str(arr[0].get("text") or arr[0].get("content") or "").strip()
                title = t.splitlines()[0][:24] or title
        except Exception:  # noqa: BLE001 — 解析不出来就用默认名
            pass
        return title, "title"

    system = next((str(m.get("content") or "") for m in msgs if m.get("role") == "system"), "")
    if "idle checkpoint reviewer" in system[:2000]:
        return "本次检查没有需要变更的候选，跳过。", "reviewer"
    return None


async def _fast_response(req: web.Request, body: dict, text: str, stream: bool,
                         tag: str) -> web.Response:
    """把本地生成的答复按 OpenAI 的两种形态返回(与网页那条路径保持一致)。"""
    model = body.get("model") or "gpt-web"
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    log(f"FASTPATH[{tag}] {len(text)}ch, 不驱动网页")
    if not stream:
        return web.json_response({
            "id": cid, "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(text) // 4,
                      "total_tokens": len(text) // 4},
        })
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8",
                                       "Cache-Control": "no-cache", "Connection": "keep-alive"})
    await resp.prepare(req)
    await resp.write(chunk(cid, model, {"role": "assistant"}).encode())
    await resp.write(chunk(cid, model, {"content": text}).encode())
    await resp.write(chunk(cid, model, {}, "stop").encode())
    await resp.write(b"data: [DONE]\n\n")
    await resp.write_eof()
    return resp


async def collect_text(base_count: int, base_text: str, deadline: float) -> str:
    """等这一条新回复写完。

    判定"是新的"用文本变化而不是消息计数: 长对话里 ChatGPT 会虚拟化旧消息,
    助手消息数量可能不增反减, 靠 count 会一直等到 deadline(实测每轮白等 300s)。
    """
    prev, stable = "", 0
    while time.monotonic() < deadline:
        snap = await snapshot(base_count)
        if snap.get("full"):
            raise ConvoFull()
        text, gen = snap.get("text", ""), snap.get("gen", False)
        fresh = bool(text.strip()) and text != base_text
        if fresh and not gen and text == prev:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        prev = text
        await asyncio.sleep(0.4)
    return prev or ""


async def collect_json(base_count: int, base_text: str, deadline: float,
                       valid_names: set[str] | None = None) -> tuple[str, int, str]:
    """收一条回复, 不合格就让模型重来:
    - 不是合法 JSON  -> REPAIRS 轮换
    - 变成"我这边没有执行器"式拒答 -> NUDGES 轮换
    - 合法 JSON 但把活写在了 content 里 -> ACTION_NUDGES 轮换
    - 调用了表里不存在的命令名 -> UNKNOWN_NUDGES 轮换
    最多重发 3 轮。"""
    text = await collect_text(base_count, base_text, deadline)
    for attempt in range(len(REPAIRS)):
        if time.monotonic() >= deadline:
            break
        obj = _extract_json_object(text)
        content, tcs = parse_reply(text)
        bad_json = obj is None
        refused = not tcs and looks_like_refusal(content or text)
        lazy = (obj is not None) and (not tcs) and looks_like_paste(content or text)
        unknown = []
        if tcs and valid_names:
            unknown = [tc["function"]["name"] for tc in tcs
                       if tc["function"]["name"] not in valid_names]
        if not bad_json and not refused and not lazy and not unknown:
            break
        if unknown:
            msg = UNKNOWN_NUDGES[attempt % 3]
            tag = f"UNKNOWN({','.join(unknown)[:40]})"
        elif refused:
            msg, tag = NUDGES[attempt % 3], "REFUSAL"
        elif lazy:
            msg, tag = ACTION_NUDGES[attempt % 3], "LAZY"
        else:
            msg, tag = REPAIRS[attempt % 3], "JSON"
        log(f"{tag}-RETRY({attempt + 1}) {text[:70]!r}")
        await type_and_send(msg)
        await asyncio.sleep(1.0)
        snap = await snapshot()
        base_text = snap.get("text", base_text)
        base_count = snap.get("count", base_count)
        text = await collect_text(base_count, base_text, deadline)
    return text, base_count, base_text


async def wait_ready(timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            last = await PAGE.eval(READY_JS)
        except Exception:
            await PAGE.close()
        if last.get("ready") == "complete" and last.get("has"):
            return
        await asyncio.sleep(1)
    raise RuntimeError(f"页面未就绪(登录可能失效): {last}")


async def new_chat(model_slug: str = "auto") -> None:
    await PAGE.ensure()
    url = CHAT_URL if model_slug in ("auto", "") else CHAT_URL + "?model=" + model_slug
    for attempt in range(3):
        try:
            await PAGE.call("Page.navigate", {"url": url}, 20)
        except Exception:
            await PAGE.close()
            await PAGE.ensure()
            await PAGE.call("Page.navigate", {"url": url}, 20)
        await wait_ready()
        # SPA 偶发吞掉导航: 若仍停留在旧会话页(/c/...), 重试导航
        if "/c/" not in await _current_url():
            return
        await asyncio.sleep(1)
    log("new_chat: 导航 3 次仍未离开旧会话页, 强制继续")


async def set_thinking(on: bool) -> str:
    """输入框里的"思考"开关: 是个 aria-pressed 的按钮, 点一下切一次。
    刚导航完工具栏可能还没渲染, 所以这里轮询等它出现。返回 'on' / 'off' / 'missing'。"""
    find = ("(() => { const b = [...document.querySelectorAll('form button')]"
            ".find(el => (el.innerText || '').trim() === '思考');"
            " if (!b) return JSON.stringify({st: 'missing'});"
            " return JSON.stringify({st: b.getAttribute('aria-pressed') === 'true' ? 'on' : 'off'}); })()")
    click = ("(() => { const b = [...document.querySelectorAll('form button')]"
             ".find(el => (el.innerText || '').trim() === '思考');"
             " if (b) b.click(); return JSON.stringify('ok'); })()")
    for _ in range(20):  # 最多等 10s
        try:
            st = (await PAGE.eval(find, 15)).get("st", "missing")
        except Exception:  # noqa: BLE001
            st = "missing"
        if st == "missing":
            await asyncio.sleep(0.5)
            continue
        if (st == "on") == on:
            return st
        await PAGE.eval(click, 15)
        await asyncio.sleep(0.6)
    return "missing"


async def type_and_send(prompt: str) -> None:
    esc = json.dumps(prompt)  # JS 字符串安全转义
    # 1) 清空残留: 上一轮若发送失败(没点掉发送键), 输入框里的旧文本会被追加上去
    js_clear = (
        f"(() => {{ const el = {COMPOSER}; el.focus();"
        f" const sel = window.getSelection(); const r = document.createRange();"
        f" r.selectNodeContents(el); sel.removeAllRanges(); sel.addRange(r);"
        f" document.execCommand('delete');"
        f" return JSON.stringify({{left: el.innerText.length}}); }})()"
    )
    await PAGE.eval(js_clear, 30)
    # 2) 插入正文
    js_insert = (
        f"(() => {{ const el = {COMPOSER}; el.focus();"
        f" document.execCommand('insertText', false, {esc});"
        f" return JSON.stringify({{len: el.innerText.length}}); }})()"
    )
    r = await PAGE.eval(js_insert, 60)
    got = r.get("len", 0)
    if got < len(prompt) * 0.8:
        raise RuntimeError(f"输入疑似被截断: 目标{len(prompt)}字符, 实际插入{got}")
    # 3) 发送: 优先点发送键(等它从"禁用"变出来), 否则用真实 Enter 键
    js_click = (
        "(() => { const b = " + SEND_BTN + ";"
        " if (!b) return JSON.stringify({ok:false});"
        " if (b.disabled) return JSON.stringify({ok:false, disabled:true});"
        " b.click(); return JSON.stringify({ok:true}); })()"
    )
    for _ in range(20):  # 最多等 4s 让发送键出现
        r = await PAGE.eval(js_click, 20)
        if r.get("ok"):
            return
        await asyncio.sleep(0.2)
    # 兜底: 真实 Enter(keydown/keyup), ProseMirror 的 keymap 会提交
    await PAGE.call("Input.dispatchKeyEvent", {
        "type": "keyDown", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r"})
    await PAGE.call("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})


ATTACH_JS_TPL = """
(async () => {
  const b64 = "__B64__";
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  const file = new File([arr], "upload.png", {type: "__MIME__"});
  const dt = new DataTransfer();
  dt.items.add(file);
  const input = document.querySelector('input[type="file"]');
  if (!input) return JSON.stringify({ok:false, err:"no input[type=file]"});
  input.files = dt.files;
  input.dispatchEvent(new Event("change", {bubbles:true}));
  return JSON.stringify({ok:true, n: input.files.length});
})()
"""


def extract_images(messages: list) -> list:
    """取最后一条 user 消息里的图片(data URL), 返回 [(b64, mime)], 最多 3 张"""
    out = []
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            return out
        for part in c:
            if not isinstance(part, dict):
                continue
            url = None
            if part.get("type") == "image_url":
                iu = part.get("image_url")
                url = iu.get("url") if isinstance(iu, dict) else (iu if isinstance(iu, str) else None)
            elif part.get("type") == "input_image":
                iu = part.get("image_url")
                url = iu.get("url") if isinstance(iu, dict) else (iu if isinstance(iu, str) else None)
            if url and url.startswith("data:image") and "," in url:
                head, b64 = url.split(",", 1)
                mime = head[5:head.find(";")] or "image/png"
                if len(b64) <= 6_000_000:
                    out.append((b64, mime))
                if len(out) >= 3:
                    return out
        return out
    return out


async def attach_images(images: list) -> int:
    """把图片塞进网页输入框的文件控件(DataTransfer), 返回成功张数"""
    ok = 0
    for b64, mime in images:
        js = ATTACH_JS_TPL.replace("__B64__", b64).replace("__MIME__", mime)
        try:
            r = await PAGE.eval(js, 60)
            if r.get("ok"):
                ok += 1
                await asyncio.sleep(1.2)  # 等附件缩略图注册
            else:
                log(f"attach 失败: {r}")
        except Exception as e:  # noqa: BLE001
            log(f"attach 异常: {e!r}")
    return ok


MODELS_JS = """
(async () => {
  const s = await fetch('/api/auth/session',{headers:{'accept':'application/json'}}).then(r=>r.json());
  const H = {'authorization':'Bearer '+(s.accessToken||''),'accept':'application/json'};
  const d = await fetch('/backend-api/models?history_and_training_disabled_items=false',{headers:H})
    .then(r=>r.json());
  const slugs = (d.models||d.items||[]).map(m=>m.slug).filter(Boolean);
  slugs.push('auto');
  return JSON.stringify({slugs: [...new Set(slugs)]});
})()
"""


async def account_models(max_age: float = 600) -> list[str]:
    """账号可用模型(10min 缓存), 失败回退兜底列表。"""
    global _models_cache, _models_ts
    if _models_cache and time.monotonic() - _models_ts < max_age:
        return _models_cache
    try:
        await PAGE.ensure()
        r = await PAGE.eval(MODELS_JS, 20)
        slugs = [s for s in r.get("slugs", []) if s and s not in MODEL_EXCLUDE]
        if slugs:
            _models_cache = slugs
            _models_ts = time.monotonic()
    except Exception as e:  # noqa: BLE001 — 缓存失败时回退静态列表, 不影响主流程
        log(f"fetch-models 失败: {e!r}")
    clean = [s for s in (_models_cache or list(MODEL_FALLBACK)) if s not in MODEL_EXCLUDE]
    return clean or list(MODEL_FALLBACK)


# ---------------- OpenAI 格式层 ----------------

# DSH 每轮会往消息末尾追加好几坨提醒(工作目录/技能表/时间/MNEMON 记忆快照),
# 但它们排在真正的任务之后 —— 网页模型看的是"最后说了什么", 于是第一轮经常回一句
# "请给出具体任务"。这里把这些背景消息挪到前面, 让真正的任务紧贴尾部提醒。
BOILER_PREFIXES = (
    "<system-reminder>",
    "Time sampled while preparing",
    "[MNEMON]",
    "MNEMON RUNTIME MEMORY SNAPSHOT",
    "MNEMON VIEW TOOLS",
    "[dsh-mnemon",
)


def _is_boiler(m: dict) -> bool:
    if m.get("role") != "user":
        return False
    c = m.get("content")
    if not isinstance(c, str):
        return False
    return c.lstrip().startswith(BOILER_PREFIXES)


# 环境级失败(工作区权限/沙箱)不该让模型反复换姿势重试 —— 实测它连试了 4 轮。
ENV_FAIL_RE = re.compile(
    r"acl|grantWrite|sandbox|access is denied|拒绝访问|权限不足|workspace grant failed", re.I)


def _history_hint(messages: list) -> str:
    """从对话历史里提炼一句"别再这么干了"的提示, 粘在任务后面。"""
    tools = [m for m in messages if m.get("role") == "tool"]
    if not tools:
        return ""
    last = str(tools[-1].get("content") or "")
    # ① 环境级失败: 换个参数重试没用, 该报告
    if ENV_FAIL_RE.search(last[:800]):
        return ("【本地提示】上一条工具结果是环境权限错误，不是命令写错了。"
                "换参数、换命令名重试都不会成功；请直接告诉用户你被权限挡住、卡在哪一步。")
    # ② 连续两条同样的报错: 别再重试同一招
    if len(tools) >= 2:
        prev = str(tools[-2].get("content") or "")
        if last.strip() and last.strip() == prev.strip() and len(last) > 20:
            return ("【本地提示】最近两次工具结果一模一样，说明同一条命令重复失败。"
                    "别再重试同一招，换思路或者直接说明卡点。")
    return ""


def _render(m: dict) -> str:
    role = m.get("role", "user")
    c = m.get("content", "")
    if isinstance(c, list):
        txt, n_img = [], 0
        for x in c:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "text":
                txt.append(str(x.get("text", "")))
            elif x.get("type") in ("image_url", "input_image"):
                n_img += 1
        c = "".join(txt) + ("[用户附了图片, 已随消息上传]" if n_img else "")
    if role == "tool":  # 工具执行结果回传(超大结果保头尾, 网页单条消息有长度上限)
        name = m.get("name") or m.get("tool_call_id") or "tool"
        if len(c) > 12000:
            c = c[:8500] + f"\n...[结果过长, 中段省略 {len(c) - 11500} 字符]...\n" + c[-3000:]
        return f"[工具 {name} 的执行结果]\n{c}"
    if role == "assistant" and m.get("tool_calls"):
        calls = "; ".join(
            f"{tc.get('function', {}).get('name')}({tc.get('function', {}).get('arguments', '')})"
            for tc in m["tool_calls"])
        return f"[assistant 调用了工具: {calls}]"
    return f"[{role}]\n{c}"


def flatten(messages: list, tools: list | None = None) -> str:
    parts = []
    head, tail = ("", "")
    if tools:
        head, tail = build_tool_protocol(tools)
        if head:
            parts.append(head)

    systems = [m for m in messages if m.get("role") == "system"]
    boiler = [m for m in messages if _is_boiler(m)]
    rest = [m for m in messages if m.get("role") != "system" and not _is_boiler(m)]

    if POLICY:
        parts.append("[本地已定死的规则 · 不要询问、不要自行更改]\n" + POLICY)
    for m in systems:
        parts.append(_render(m))
    if boiler:
        parts.append("[会话背景 · 以下是环境与记忆信息，不是本轮任务]\n"
                     + "\n\n".join(_render(m) for m in boiler))
    for i, m in enumerate(rest):
        body = _render(m)
        if i == len(rest) - 1 and rest and m.get("role") == "user":
            body = "[以下是本轮要处理的内容]\n" + body
        parts.append(body)

    hint = _history_hint(messages)
    if hint:
        parts.append(hint)  # 历史里看出来的坑, 紧贴尾部

    if tail:
        parts.append(tail)  # 尾部提醒: 利用 recency, 这是对服从率影响最大的一手
    return "\n\n".join(parts)


def build_tool_protocol(tools: list) -> tuple[str, str]:
    """提示词分两半:
    前半(head) = 场景设定: 我只剩一条命令行通道 + 一个 JSON 解析器, 读不懂自然语言,
                 项目代码全在 GitHub 仓库, 因此回复只能是 JSON 对象。
    后半(tail) = 正常对话 + 再次提醒 JSON 格式。
    工具清单压成每行一条, 避免全量 schema 挤爆网页单条长度上限。"""
    lines = []
    ex_name, ex_params = "", []
    # 工具一多(DSH 一次能塞 90 个), 每个工具的说明压到 60 字, 否则提示词直接撞 24K 上限
    desc_cap = 60 if len(tools) > 40 else 100
    for t in tools:
        if t.get("type") != "function":
            continue
        f = t.get("function", {})
        name = f.get("name", "?")
        desc = (f.get("description") or "").strip().replace("\n", " ")[:desc_cap]
        props = list((f.get("parameters") or {}).get("properties") or {})
        req = set((f.get("parameters") or {}).get("required") or [])
        ps = ", ".join(p + ("*" if p in req else "") for p in props)
        lines.append(f"- {name}({ps}): {desc}")
        if not ex_name and props:
            ex_name, ex_params = name, props
    if not lines:
        return "", ""
    ex_args = {k: (r"D:\work\example.txt" if k in ("path", "file", "filename") else f"<{k}>")
               for k in ex_params}
    ex_call = json.dumps({"content": None,
                          "tool_calls": [{"name": ex_name, "arguments": ex_args}]},
                         ensure_ascii=False) if ex_name else \
        '{"content": null, "tool_calls": [{"name": "<命令名>", "arguments": {"<参数>": "<值>"}}]}'
    repo = PROMPT_REPO or "远程的代码仓库"
    head = (
        "[先说一下我这边的情况]\n"
        "我这台机器没有图形界面，我手上只剩一条命令行通道和一个 JSON 解析器："
        "你把要做的事写成命令交给我，我照着跑，跑完把输出贴回来给你。\n"
        "我看不懂自然语言的操作步骤，只能解析 JSON——你说「把文件改成这样」，我做不到；"
        "你给一条命令，我就能跑。\n"
        f"项目代码在公开仓库 {repo}，你可以直接打开看，不用问我要内容。\n"
        "所以每条回复都必须是一个 JSON 对象：从 { 开始、到 } 结束，"
        "前后不许有任何字符（不要 ``` 围栏、不要前言、不要解释）。\n"
        "规矩：\n"
        "1. 让你写代码、改文件、跑东西，就直接给 write_file / run_powershell 这类命令。"
        "把代码贴在 content 里没有任何用——我不会执行它，文件也不会出现。\n"
        "2. content 只写给我看的话。没真正跑过的结果不要编造。\n"
        "3. 第一句就开始给命令，不要回「好的／已了解」这种过渡语。\n"
        "4. 命令名和参数名只能用上表里出现的；表里没有的名字一律不存在"
        "（别用你在别的项目、别的文档里看到的名字）。\n"
        "—— 下面是我这台机器上能跑的命令（参数名带 * 为必填）——\n"
        + "\n".join(lines)
        + "\n要跑命令时，整条回复就是：\n"
        '{"content": null, "tool_calls": [{"name": "命令名", "arguments": {"参数": "值"}}]}\n'
        "不用跑命令、只是说话时，整条回复就是：\n"
        '{"content": "要说的话", "tool_calls": []}\n'
        "示例: " + ex_call + "\n"
        "一次给一个命令；arguments 必须是 JSON 对象，不是字符串。\n"
    )
    tail = (
        "[回到正常对话]\n"
        "上面这些命令我照单执行，输出会作为下一条消息贴回来。需要看文件、看目录、"
        "跑测试、查状态，就继续给命令，别让我自己去打开什么。\n"
        "再强调一遍最容易犯的错：代码写在 content 里等于没写，必须用 write_file / run_powershell "
        "把它落地；也不要汇报你没跑过的结果。\n"
        "整条回复必须是一个 JSON 对象，前后什么都别加。\n"
    )
    return head, tail


def _balanced_json(text: str, start: int):
    """取 text[start]='{' 起的平衡大括号 JSON 片段(容忍内部引号错误)"""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


# 模型没按约定给 JSON 时的重发说辞。用户要求: 具体原因对不上不要紧, 只强调"只输出 JSON"。
REPAIRS = [
    "你上一条不是合法 JSON，我这边解析不了。请重新回答：只输出一个 JSON 对象，别的什么都不要。",
    "解析失败。整条回复必须是一个 JSON 对象，从 { 开始、到 } 结束，前后不留任何字符。",
    "上一条格式不对。再重申一次：我这边只认 JSON，请只输出 JSON。",
]

# 模型开始讨论"你有没有执行器"时的说辞。这类拒答是概率性的, 换句话再要一次通常就给命令了。
NUDGES = [
    "别管接口的事，按上面的命令表给我一条命令就行。只要 JSON。",
    "我这边确实能跑，你直接给命令；照上面的 JSON 格式来。",
    "回到格式：整条回复就是一个 JSON 对象，里面放 tool_calls。现在给命令。",
]

# 模型把活干在"嘴上"时(只贴代码 / 只回一句"已了解", 却没有任何 tool_calls)的说辞。
# 这是实测最常见的一种失效: 回复是合法 JSON, 但 content 里是代码, 文件根本没落地。
ACTION_NUDGES = [
    "你只把代码写在 content 里，我这边什么都没落地。请用 write_file / run_powershell "
    "把文件真正写出来，再把结果告诉我。只输出 JSON。",
    "没有 tool_calls 我什么都干不了。把要做的事变成一条命令放进 tool_calls，只输出 JSON。",
    "别在 content 里贴代码——贴了不会执行。现在给我一条命令（tool_calls），只输出 JSON。",
]

# "只说话不动手"的痕迹: 贴了代码块、给了运行结果、或者只是确认收到
PASTE_RE = re.compile(
    r"```|^\s*def\s+\w+\s*\(|^\s*(import|from)\s+\w+|运行结果|输出结果|已了解|好的[，,。]|收到[，,。]|明白[，,。]",
    re.M)


def looks_like_paste(text: str) -> bool:
    """有内容但没有 tool_calls, 且内容像是"把活写在了回答里"。"""
    return bool(text) and bool(PASTE_RE.search(text[:3000]))


REFUSAL_RE = re.compile(
    r"(?:没有|未)(?:连接|接入|提供|可用)[^。\n]{0,30}(?:执行器|接口|工具|环境)"
    r"|(?:执行器|接口|工具)[^。\n]{0,12}(?:没有|未)(?:连接|接入|提供)"
    r"|无法[^。\n]{0,10}(?:访问|执行|运行|读取|连接)"
    r"|不能[^。\n]{0,10}(?:访问|执行|运行)"
    r"|不会(?:伪造|编造|谎称|声称|假装)"
    r"|请提供[^。\n]{0,12}(?:执行器|工具|连接|接口)"
    r"|I (?:can'?t|cannot|do not have)[^.\n]{0,24}(?:access|execute|run|connect)",
    re.I)


def looks_like_refusal(text: str) -> bool:
    return bool(text) and bool(REFUSAL_RE.search(text[:800]))


# 模型调了表里没有的命令名(实测: 它从别的项目文档里抄了 run_powershell)时的说辞。
UNKNOWN_NUDGES = [
    "你用的命令名不在这张表里，我这边没有这个命令，执行不了。只能从上表列出的名字里选一个，重来。",
    "没有这个命令。请严格用上表的命令名和参数名，只输出 JSON。",
    "这个名字不存在。回到命令表：只用表里有的名字，给一条命令。",
]


def _extract_json_object(text: str):
    """从模型回复中取出第一个平衡的 JSON 对象; 容忍 ``` 围栏与前后杂字。"""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[A-Za-z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s).strip()
    i = s.find("{")
    if i < 0:
        return None
    frag = _balanced_json(s, i)
    if not frag:
        return None
    for candidate in (frag, frag.replace("\r\n", "\\n").replace("\n", "\\n")):
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _norm_call(c: dict):
    """把一条工具调用规整成 OpenAI tool_call; 兼容 {name,arguments} 与 {function:{...}}。"""
    fn = c.get("function") if isinstance(c.get("function"), dict) else c
    name = fn.get("name") or fn.get("tool") or fn.get("command")
    args = fn.get("arguments", fn.get("args", fn.get("parameters", {})))
    if not name:
        return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            pass
    return {"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
            "function": {"name": str(name),
                         "arguments": args if isinstance(args, str)
                         else json.dumps(args, ensure_ascii=False)}}


def parse_reply(text: str):
    """把模型的一条回复解析成 (content, tool_calls)。

    约定: 整条回复是一个 JSON 对象
        {"content": "..." | null, "tool_calls": [{"name": "...", "arguments": {...}}]}
    另兼容: OpenAI 原形(带 function.arguments 字符串)、{"message": ...}、
    {"commentary": [...]}(无 content 时当正文)。完全不是 JSON 就原样当 content。
    """
    obj = _extract_json_object(text)
    if obj is None:
        return text, None
    content = obj.get("content")
    if content is None:
        content = obj.get("message") or obj.get("reply") or obj.get("text")
    if content is None and obj.get("commentary"):
        cm = obj["commentary"]
        content = "\n".join(str(x) for x in cm) if isinstance(cm, list) else str(cm)
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False)
    calls = obj.get("tool_calls")
    if calls is None:
        calls = obj.get("tools") or []
    if isinstance(calls, dict):
        calls = [calls]
    # 兼容另一种常见形态: {"instructions": [{"action": "write_file", "path": ..., "content": ...}]}
    if not calls and isinstance(obj.get("instructions"), list):
        calls = [
            {"name": it.get("action") or it.get("name") or it.get("command"),
             "arguments": {k: v for k, v in it.items() if k not in ("action", "id", "name")}}
            for it in obj["instructions"] if isinstance(it, dict)
        ]
    tcs = [tc for tc in (_norm_call(c) for c in calls if isinstance(c, dict)) if tc] \
        if isinstance(calls, list) else []
    return content, (tcs or None)


PROMPT_CAP = 24000  # 网页单条消息硬上限 ~27K chars(实测), 留 10% 边际; 30K 中文/27K 英文数据见 git 历史


def truncate_messages(messages: list, budget: int = PROMPT_CAP - 600) -> list:
    """裁到字符预算内: 超长 system 头部裁剪 -> 从最旧的中间消息丢起 -> 永远保最后一条。"""

    def _content(m):
        c = m.get("content", "")
        if isinstance(c, list):
            c = "".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
        return str(c)

    def _size(m):
        return len(_content(m))

    msgs = [dict(m) for m in messages]
    if not msgs:
        return msgs

    # 1) 首条(system)本身过大: 保头 55% + 尾 15% (尾部常含环境信息), 中段省略
    if _size(msgs[0]) > budget * 0.7:
        c = _content(msgs[0])
        cut_h, cut_t = int(budget * 0.55), int(budget * 0.15)
        msgs[0]["content"] = (c[:cut_h] + "\n[...系统提示中段因过长省略...]\n" + c[-cut_t:])

    # 2) 从最旧的中间消息开始丢, 直到总量达标(首尾保留)
    i = 1
    while sum(_size(m) for m in msgs) > budget and len(msgs) > 2 and i < len(msgs) - 1:
        msgs.pop(i)

    # 3) 最后一条自身超长: 尾部裁剪
    if sum(_size(m) for m in msgs) > budget:
        last = msgs[-1]
        others = sum(_size(m) for m in msgs[:-1])
        keep = max(2000, budget - others)
        last["content"] = _content(last)[:keep] + "\n[最新消息因超长被截断]"
    return msgs


async def _keepalive(resp: web.StreamResponse, interval: float = 10.0) -> None:
    """SSE 注释帧保活: 工具模式下收网页回复可能要几十秒没字节, 客户端会按空闲超时断开。"""
    try:
        while True:
            await asyncio.sleep(interval)
            await resp.write(b": ping\n\n")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — 客户端已断开, 结束即可
        return


def chunk(cid: str, model: str, delta: dict, finish=None) -> str:
    d = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
         "model": model,
         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def err(message: str, code: int = 500) -> web.Response:
    return web.json_response({"error": {"message": message, "type": "gpt_bridge_error",
                                        "code": code}}, status=code)


LIMIT_RE = re.compile(
    r"hit your (?:usage )?limit|you'?ve reached your|usage (?:cap|limit)|rate limit"
    r"|已达到.{0,6}(上限|限制)|使用额度", re.I)


def is_limit_text(text: str) -> bool:
    return bool(text) and bool(LIMIT_RE.search(text[:600]))


async def models(_: web.Request) -> web.Response:
    slugs = await account_models()
    data = []
    if "auto" not in slugs:
        slugs = ["auto"] + slugs
    for s in ["gpt-web"] + slugs:  # gpt-web = auto 的兼容别名
        if s == "gpt-web" or s not in [d["id"] for d in data]:
            data.append({"id": s, "object": "model", "owned_by": "chatgpt-web"})
    return web.json_response({"object": "list", "data": data})


async def health(_: web.Request) -> web.Response:
    return web.json_response({
        "ok": True, "version": "0.3.0", "model": "gpt-web",
        "auth": "bearer", "sessions": len(SESSIONS),
        "prompt_cap": PROMPT_CAP,
    })


async def _completion(req: web.Request, body: dict, allow_retry: bool = True) -> web.Response:
    messages = body.get("messages") or []
    if not messages:
        return err("messages required", 400)
    model = body.get("model") or "gpt-web"
    stream = bool(body.get("stream"))

    # 能在本地直接答的(会话标题、Mnemon 空闲审查)就别去驱动网页
    fast = _fast_answer(body)
    if fast is not None:
        return await _fast_response(req, body, fast[0], stream, fast[1])

    slug = "auto" if model in ("gpt-web", "", "auto") else model
    available = await account_models()
    if slug not in available and model != "gpt-web":
        return err(f"未知模型 {model}。可用: {', '.join(available)} (gpt-web=auto)", 400)
    tools = body.get("tools") or []
    # 思考开关: 请求头 X-Thinking 优先, 否则用 env 默认; None = 不碰页面上的开关
    _hdr = req.headers.get("X-Thinking", "").strip().lower()
    want_thinking = (True if _hdr in ("1", "true", "on", "yes")
                     else False if _hdr in ("0", "false", "off", "no")
                     else (True if THINKING else None))
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    started = time.time()
    resp: web.StreamResponse | None = None

    # 增量会话: 指纹链 = 首条(system)之外每条消息的哈希; 链前缀匹配则只发增量
    skey = _session_key(slug, messages, tools)
    chain = [_msg_fp(m) for m in messages[1:]]

    async with BUSY:  # 单账号单并发, 排队等待
        prompt = ""
        delta_text = ""
        base: dict = {}
        send_err = None
        for send_attempt in range(2):  # 瞬时故障(页面卡载等)自动重试一次
            sess, delta_start = _find_session(skey, chain)
            mode = "delta" if sess else "full"
            try:
                if mode == "delta":
                    # 续聊: 回到已存在的网页会话, 只追加新消息(网页自己记得历史)
                    await PAGE.ensure()
                    if await _current_url() != sess["url"]:
                        await PAGE.call("Page.navigate", {"url": sess["url"]}, 25)
                        await wait_ready()
                    base = await snapshot()
                    delta_msgs = messages[1 + delta_start:]
                    delta_text = flatten(delta_msgs, None)
                    if tools:
                        _, tail_p = build_tool_protocol(tools)
                        delta_text += "\n\n" + tail_p
                    else:
                        tail_p = ""
                    if len(delta_text) > PROMPT_CAP:
                        # 只截中段, 尾部那段"必须给命令"的提醒永远保住
                        room = max(1000, PROMPT_CAP - len(tail_p) - 60)
                        delta_text = delta_text[:room] + "\n...[增量过长, 中段截断]...\n" + tail_p
                    log(f"REQ: model={model} stream={int(stream)} tools={len(tools)} "
                        f"mode=delta +{len(delta_msgs)}msg {len(delta_text)}ch")
                    images = extract_images(delta_msgs)
                    if images:
                        log(f"IMG: attach {len(images)} 张(delta)")
                        await attach_images(images)
                    await type_and_send(delta_text)
                else:
                    head_p, tail_p = build_tool_protocol(tools) if tools else ("", "")
                    proto_len = len(head_p) + len(tail_p)
                    msgs = truncate_messages(messages, budget=max(8000, PROMPT_CAP - proto_len - 500))
                    prompt = flatten(msgs, tools)
                    shrink = 3000
                    while len(prompt) > PROMPT_CAP and shrink <= 12000:
                        msgs = truncate_messages(messages, budget=max(6000, PROMPT_CAP - proto_len - 500 - shrink))
                        prompt = flatten(msgs, tools)
                        shrink += 3000
                    if len(prompt) > PROMPT_CAP:
                        # 同上: 保住尾部的命令提醒, 只截中段
                        room = max(1000, PROMPT_CAP - len(tail_p) - 60)
                        prompt = prompt[:room] + "\n...[提示词过长, 中段截断]...\n" + tail_p
                    log(f"REQ: model={model} stream={int(stream)} tools={len(tools)} "
                        f"mode=full msgs={len(messages)} prompt={len(prompt)}ch")
                    await new_chat(slug)
                    # "思考"开关随新对话重置, 这里按 env / 请求头再设一次
                    if want_thinking is not None:
                        log(f"思考模式: {await set_thinking(want_thinking)}")
                    images = extract_images(msgs)
                    if images:
                        log(f"IMG: attach {len(images)} 张(full)")
                        await attach_images(images)
                    base = await snapshot()
                    await type_and_send(prompt)
                send_err = None
                break
            except Exception as e:  # noqa: BLE001
                send_err = e
                if sess:
                    SESSIONS.pop(sess["sid"], None)
                await PAGE.close()
                if send_attempt == 0:
                    log(f"SEND-RETRY({slug},{mode}): {e!r}")
                    await asyncio.sleep(3)
        if send_err is not None:
            log(f"SEND-FAIL({slug}): {send_err!r}")
            return err(f"网页操作失败: {send_err}")
        base_count = base.get("count", 0)
        base_text = base.get("text", "")

        try:
            conv_url = ""
            deadline = time.monotonic() + 300
            if not stream:
                if tools:
                    _names = {t.get("function", {}).get("name") for t in tools}
                    text, base_count, base_text = await collect_json(
                        base_count, base_text, deadline, _names)
                else:
                    text = await collect_text(base_count, base_text, deadline)
                conv_url = await _current_url()
                if is_limit_text(text):
                    log(f"LIMIT({slug},{mode}) {time.time()-started:.1f}s")
                    return err("ChatGPT 账号免费额度已用尽(网页返回限流提示)。"
                               "通常几小时后重置; 或改用 mini 档模型(限额独立)。", 429)
                content, tcs = (None, None)
                if tools:
                    content, tcs = parse_reply(text)
                msg: dict = {"role": "assistant"}
                finish = "stop"
                if tcs:
                    msg["content"] = content
                    msg["tool_calls"] = tcs
                    finish = "tool_calls"
                else:
                    msg["content"] = text if content is None else content
                log(f"OK non-stream({slug},{mode},{'tools' if tcs else 'chat'}) "
                    f"{len(text)}B {time.time()-started:.1f}s")
                out = {
                    "id": cid, "object": "chat.completion", "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                    "usage": {"prompt_tokens": len(prompt or delta_text) // 4,
                              "completion_tokens": len(text) // 4,
                              "total_tokens": (len(prompt or delta_text) + len(text)) // 4},
                    "session_mode": mode,
                }
                if conv_url:
                    out["conversation_url"] = conv_url
                _remember(sess, skey, chain, conv_url)
                return web.json_response(out)

            resp = web.StreamResponse(headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache", "Connection": "keep-alive"})
            # 流式限流预检: 发送后若立即出现限流文案, 直接回 429 JSON 而非 SSE
            for _ in range(16):
                psnap = await snapshot(base_count)
                if psnap.get("full"):
                    raise ConvoFull()
                if is_limit_text(psnap.get("text", "")):
                    log(f"LIMIT({slug},{mode}) pre-stream {time.time()-started:.1f}s")
                    return err("ChatGPT 账号免费额度已用尽(网页返回限流提示)。"
                               "通常几小时后重置; 或改用 mini 档模型(限额独立)。", 429)
                if psnap.get("text", "").strip():
                    break
                await asyncio.sleep(0.5)
            await resp.prepare(req)
            await resp.write(chunk(cid, model, {"role": "assistant"}).encode())
            if tools:
                # 工具模式: 先收全网页输出(必要时让模型重发 JSON), 再统一转 SSE。
                # 收的时候可能几十秒没字节, 客户端(DSH 默认 300s)会按空闲超时断开,
                # 所以边收边发 SSE 注释帧保活。
                _names = {t.get("function", {}).get("name") for t in tools}
                pinger = asyncio.create_task(_keepalive(resp))
                try:
                    text, base_count, base_text = await collect_json(
                        base_count, base_text, deadline, _names)
                finally:
                    pinger.cancel()
                content, tcs = parse_reply(text)
                if tcs:
                    await resp.write(chunk(cid, model, {"tool_calls": [
                        {"index": 0, **tc} for tc in tcs]}).encode())
                    await resp.write(chunk(cid, model, {}, "tool_calls").encode())
                else:
                    body = text if content is None else content
                    for i in range(0, len(body), 64):
                        await resp.write(chunk(cid, model,
                                               {"content": body[i:i + 64]}).encode())
                    await resp.write(chunk(cid, model, {}, "stop").encode())
                log(f"OUT[{slug}]: {text[:200]!r}")
                log(f"OK stream({slug},{mode},{'tools' if tcs else 'chat'}) "
                    f"{len(text)}B {time.time()-started:.1f}s")
            else:
                sent, prev, stable = "", "", 0
                while time.monotonic() < deadline:
                    snap = await snapshot(base_count)
                    if snap.get("full"):
                        raise ConvoFull()
                    text, gen = snap.get("text", ""), snap.get("gen", False)
                    fresh = bool(text) and text != base_text
                    if fresh and len(text) > len(sent) and text.startswith(sent):
                        await resp.write(chunk(cid, model,
                                               {"content": text[len(sent):]}).encode())
                        sent = text
                    if fresh and not gen and text == prev:
                        stable += 1
                        if stable >= 2:
                            break
                    else:
                        stable = 0
                    prev = text
                    await asyncio.sleep(0.4)
                await resp.write(chunk(cid, model, {}, "stop").encode())
                log(f"OK stream({slug},{mode}) {len(sent)}B {time.time()-started:.1f}s")
            conv_url = await _current_url()
            _remember(sess, skey, chain, conv_url)
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        except ConvoFull:
            if sess:
                SESSIONS.pop(sess["sid"], None)
            log(f"CONVO-FULL({slug},{mode}) {time.time()-started:.1f}s")
            # 非流式且尚未写出任何东西: 让外层丢掉会话、重建对话再跑一次
            if allow_retry and not stream:
                raise
            sse_err = ("data: " + json.dumps({"error": {"message": "网页会话已达长度上限, 会话已失效; 重试将自动重建新会话",
                       "type": "gpt_bridge_error", "code": 429}}, ensure_ascii=False) + "\n\ndata: [DONE]\n\n")
            if resp is not None and resp.prepared:
                try:
                    await resp.write(sse_err.encode())
                    await resp.write_eof()
                except Exception:
                    pass
                return resp
            return err("网页会话已达长度上限, 会话已失效; 重试将自动重建新会话", 429)
        except asyncio.CancelledError:
            log(f"CLIENT-CANCEL({slug},{mode}) {time.time()-started:.1f}s")
            raise
        except Exception as e:  # noqa: BLE001
            await PAGE.close()
            log(f"COLLECT-FAIL({slug},{mode}): {e!r}")
            if resp is not None and resp.prepared:
                try:
                    await resp.write(err(f"采集失败: {e}").text.encode())
                except Exception:
                    pass
                return resp
            return err(f"采集失败: {e}")

async def completions(req: web.Request) -> web.Response:
    """入口: 鉴权 → 解析 → 跑一次 → 记一行日志; 撞长度上限就换个新对话重跑一次。"""
    if req.headers.get("Authorization") != f"Bearer {KEY}":
        return err("Invalid API key", 401)
    try:
        body = json.loads(await req.read())
    except Exception:
        return err("bad json", 400)

    t0 = time.time()
    status = "ok"
    if os.environ.get("GPTWEB_DUMP", "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            (GW / "last_request.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    try:
        resp = await _completion(req, body, allow_retry=True)
        status = f"http{resp.status}"
        # 兼容: 有些客户端要求 id 以 chatcmpl- 开头且 usage 存在, 这里不动
        return resp
    except ConvoFull:
        # 会话被顶满: 丢掉所有会话映射, 强制走"新建对话 + 全量重发"
        SESSIONS.clear()
        log("CONVO-FULL → 丢会话, 新建对话重跑一次")
        try:
            resp = await _completion(req, body, allow_retry=False)
            status = f"http{resp.status}(rebuilt)"
            return resp
        except Exception as e:  # noqa: BLE001
            status = f"rebuild-fail:{type(e).__name__}"
            log(f"REBUILD-FAIL: {e!r}")
            return err("网页会话已达长度上限, 重建对话后仍失败; 请重试或减少上下文", 429)
    except Exception as e:  # noqa: BLE001
        status = f"exc:{type(e).__name__}"
        raise
    finally:
        _log_request(body, status, time.time() - t0)


async def new_chat_api(req: web.Request) -> web.Response:
    """显式开一个新对话(不改动调用方的 messages)。agent 的 --new-chat 走这里。"""
    if req.headers.get("Authorization") != f"Bearer {KEY}":
        return err("Invalid API key", 401)
    try:
        body = json.loads(await req.read() or b"{}")
    except Exception:
        body = {}
    async with BUSY:
        await new_chat(body.get("model") or "auto")
        url = await _current_url()
    log(f"NEW-CHAT → {url}")
    return web.json_response({"ok": True, "url": url})


app = web.Application(client_max_size=10 * 1024 * 1024)
app.router.add_get("/v1/models", models)
app.router.add_get("/health", health)
app.router.add_post("/v1/chat/completions", completions)
app.router.add_post("/new_chat", new_chat_api)

if __name__ == "__main__":
    log(f"gpt-bridge 启动 :{PORT} key={KEY[:12]}…")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)
