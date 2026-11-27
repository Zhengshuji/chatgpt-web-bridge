#!/usr/bin/env python3
"""CDP 探针：看 chatgpt.com 那个标签页现在是什么状态。"""
import asyncio
import json
import sys

import aiohttp
import websockets

CDP = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9225"

JS = r"""(() => {
  const q = s => document.querySelector(s);
  const composer = q('#prompt-textarea') || q('div[contenteditable="true"][role="textbox"]');
  return JSON.stringify({
    url: location.href,
    title: document.title,
    ready: document.readyState,
    composer: !!composer,
    send: !!q('[data-testid="send-button"], button[aria-label*="Send"]'),
    stop: !!q('[data-testid="stop-button"]'),
    assistants: document.querySelectorAll('[data-message-author-role="assistant"]').length,
    body: (document.body.innerText || '').slice(0, 400)
  });
})()"""


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{CDP}/json/list") as r:
            tabs = await r.json()
    pages = [t for t in tabs if t.get("type") == "page" and "chatgpt.com" in t.get("url", "")]
    print(f"chatgpt tabs: {len(pages)}")
    if not pages:
        print("no chatgpt tab. all pages:")
        for t in tabs:
            if t.get("type") == "page":
                print("  -", t.get("title"), "|", t.get("url"))
        return
    ws_url = pages[0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=20 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": JS, "returnByValue": True, "awaitPromise": True}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                res = msg.get("result", {}).get("result", {})
                raw = res.get("value")
                if raw is None:
                    print("eval failed:", json.dumps(res, ensure_ascii=False)[:500])
                else:
                    d = json.loads(raw)
                    for k, v in d.items():
                        print(f"{k:10}: {v}")
                return


asyncio.run(main())
