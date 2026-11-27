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
    elif url:
        _save_session(key, chain, url)


async def _current_url() -> str:
    try:
        r = await PAGE.eval("JSON.stringify({u: location.href})")
        return r.get("u", "")
    except Exception:
        return ""


# ---------------- 网页操作 ----------------

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


async def collect_json(base_count: int, base_text: str, deadline: float) -> tuple[str, int, str]:
    """收一条回复, 不合格就让模型重来:
    - 不是合法 JSON  -> REPAIRS 轮换
    - 变成了"我这边没有执行器"式拒答 -> NUDGES 轮换
    最多重发 REPAIRS 轮。"""
    text = await collect_text(base_count, base_text, deadline)
    for attempt in range(len(REPAIRS)):
        if time.monotonic() >= deadline:
            break
        obj = _extract_json_object(text)
        content, tcs = parse_reply(text)
        bad_json = obj is None
        refused = not tcs and looks_like_refusal(content or text)
        if not bad_json and not refused:
            break
        msg = (NUDGES if refused else REPAIRS)[attempt % 3]
        log(f"{'REFUSAL' if refused else 'JSON'}-RETRY({attempt + 1}) {text[:70]!r}")
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

def flatten(messages: list, tools: list | None = None) -> str:
    parts = []
    head, tail = ("", "")
    if tools:
        head, tail = build_tool_protocol(tools)
        if head:
            parts.append(head)
    for m in messages:
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
            parts.append(f"[工具 {name} 的执行结果]\n{c}")
        elif role == "assistant" and m.get("tool_calls"):
            calls = "; ".join(
                f"{tc.get('function', {}).get('name')}({tc.get('function', {}).get('arguments', '')})"
                for tc in m["tool_calls"])
            parts.append(f"[assistant 调用了工具: {calls}]")
        else:
            parts.append(f"[{role}]\n{c}")
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
    for t in tools:
        if t.get("type") != "function":
            continue
        f = t.get("function", {})
        name = f.get("name", "?")
        desc = (f.get("description") or "").strip().replace("\n", " ")[:100]
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
        "再说一遍：整条回复必须是一个 JSON 对象，前后什么都别加。\n"
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


async def completions(req: web.Request) -> web.Response:
    if req.headers.get("Authorization") != f"Bearer {KEY}":
        return err("Invalid API key", 401)
    try:
        body = await req.json()
    except Exception:
        return err("bad json", 400)
    messages = body.get("messages") or []
    if not messages:
        return err("messages required", 400)
    model = body.get("model") or "gpt-web"
    stream = bool(body.get("stream"))
    slug = "auto" if model in ("gpt-web", "", "auto") else model
    available = await account_models()
    if slug not in available and model != "gpt-web":
        return err(f"未知模型 {model}。可用: {', '.join(available)} (gpt-web=auto)", 400)
    tools = body.get("tools") or []
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
                    if len(delta_text) > PROMPT_CAP:
                        delta_text = delta_text[:PROMPT_CAP] + "\n[增量超长已截断]"
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
                        prompt = prompt[:PROMPT_CAP] + "\n[提示词超长, 已强制截断]"
                    log(f"REQ: model={model} stream={int(stream)} tools={len(tools)} "
                        f"mode=full msgs={len(messages)} prompt={len(prompt)}ch")
                    await new_chat(slug)
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
                    text, base_count, base_text = await collect_json(base_count, base_text, deadline)
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
                # 工具模式: 先收全网页输出(必要时让模型重发 JSON), 再统一转 SSE
                text, base_count, base_text = await collect_json(base_count, base_text, deadline)
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

app = web.Application(client_max_size=10 * 1024 * 1024)
app.router.add_get("/v1/models", models)
app.router.add_get("/health", health)
app.router.add_post("/v1/chat/completions", completions)

if __name__ == "__main__":
    log(f"gpt-bridge 启动 :{PORT} key={KEY[:12]}…")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)
