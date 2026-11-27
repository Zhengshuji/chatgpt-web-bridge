#!/usr/bin/env python3
"""登录 / 换账号助手。

用法：
    python login.py                        # 用默认 profile 打开 Chrome，人肉登录，登录成功后校验
    python login.py --profile profiles/b   # 换一个账号就用另一个 profile 目录
    python login.py --check                # 只看当前登录状态，不启动浏览器

多账号 = 多个 profile 目录 + 不同调试端口。登录态是 cookie，复制 profile 也能带走。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "chrome_profile"

STATE_JS = r"""(() => {
  const composer = document.querySelector('#prompt-textarea, div.ProseMirror[contenteditable]');
  const body = (document.body.innerText || '');
  const lines = body.split('\n').map(s => s.trim()).filter(Boolean);
  const planIdx = lines.findIndex(s => /^(免费版|Plus|Pro|Team|企业版|Free)$/.test(s));
  const plan = planIdx >= 0 ? lines[planIdx] : '';
  // 账号名通常紧挨在套餐名上面一行（侧栏底部头像旁边）
  let name = '';
  if (planIdx > 0) {
    const cand = lines[planIdx - 1];
    if (cand && cand.length <= 40 && !/^(跳转到内容|ChatGPT|新聊天|探索|资料库|图像|项目|最近|升级订阅|登录)$/.test(cand)) {
      name = cand;
    }
  }
  return JSON.stringify({
    url: location.href, title: document.title, composer: !!composer,
    plan, name, body: body.slice(0, 160)
  });
})()"""


def cdp_up(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def launch(profile: Path, port: int) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([CHROME, f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
                      "--no-first-run", "--no-default-browser-check",
                      "--disable-background-timer-throttling",
                      "--disable-backgrounding-occluded-windows",
                      "--disable-renderer-backgrounding",
                      "https://chatgpt.com/"])


async def page_state(port: int) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/json/list") as r:
            tabs = await r.json()
    tab = next((t for t in tabs if t.get("type") == "page" and "chatgpt.com" in t.get("url", "")), None)
    if tab is None:
        return {"error": "没有 chatgpt.com 的标签页"}
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": STATE_JS, "returnByValue": True, "awaitPromise": True}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                raw = m.get("result", {}).get("result", {}).get("value")
                return json.loads(raw) if raw else {"error": "取不到页面状态"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE))
    ap.add_argument("--port", type=int, default=9225)
    ap.add_argument("--check", action="store_true", help="只看状态，不启动浏览器")
    ap.add_argument("--timeout", type=int, default=300, help="等待登录的秒数")
    args = ap.parse_args()
    profile = Path(args.profile).resolve()

    if not args.check and not cdp_up(args.port):
        print(f"启动 Chrome，profile = {profile}，调试端口 = {args.port}")
        launch(profile, args.port)
        for _ in range(30):
            time.sleep(1)
            if cdp_up(args.port):
                break
        else:
            print("❌ Chrome 的调试端口没起来（Chrome 可能被已有实例占用）")
            return 1

    if not cdp_up(args.port):
        print(f"❌ {args.port} 上没有 CDP，先不带 --check 跑一次")
        return 1

    deadline = time.time() + args.timeout
    last = {}
    while time.time() < deadline:
        import asyncio
        last = asyncio.run(page_state(args.port))
        if last.get("composer"):
            print("✅ 已登录，输入框就绪")
            print(f"   账号：{last.get('name') or '（未识别）'}")
            print(f"   套餐：{last.get('plan') or '（未识别）'}")
            print(f"   页面：{last.get('url')}")
            return 0
        if args.check:
            break
        print(f"… 等待登录（当前：{last.get('title') or last.get('error')}）")
        time.sleep(5)

    print("❌ 还没看到输入框：请在打开的 Chrome 里完成登录 / 过验证码，然后重跑本命令")
    print(f"   当前页面：{last.get('title')} {last.get('body', '')[:80]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
