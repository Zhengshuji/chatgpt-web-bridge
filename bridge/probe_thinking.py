"""探针：找"思考"开关，以及新对话入口。"""
import asyncio
import json
import sys

import aiohttp
import websockets

CDP = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9225"

JS = r"""(() => {
  const pick = (el) => ({
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || '').trim().slice(0, 24),
    aria: el.getAttribute('aria-label') || '',
    pressed: el.getAttribute('aria-pressed'),
    testid: el.getAttribute('data-testid') || '',
    cls: (el.className || '').toString().slice(0, 70),
    inComposer: !!el.closest('form')
  });
  const byText = [...document.querySelectorAll('button,div[role="button"],span[role="button"],label')]
    .filter(el => /思考|thinking|深度|reason/i.test(el.innerText || '') || /思考|thinking/i.test(el.getAttribute('aria-label') || ''))
    .slice(0, 12).map(pick);
  const newChat = [...document.querySelectorAll('a,button')]
    .filter(el => /新聊天|New chat/i.test((el.innerText || '') + (el.getAttribute('aria-label') || '')))
    .slice(0, 4).map(pick);
  const form = document.querySelector('form');
  return JSON.stringify({
    url: location.href,
    think: byText,
    newChat,
    formButtons: form ? [...form.querySelectorAll('button')].map(pick) : []
  });
})()"""


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{CDP}/json/list") as r:
            tabs = await r.json()
    tab = next(t for t in tabs if t.get("type") == "page" and "chatgpt.com" in t.get("url", ""))
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": JS, "returnByValue": True, "awaitPromise": True}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                d = json.loads(m["result"]["result"]["value"])
                print("URL:", d["url"])
                print("\n== 思考相关 ==")
                for x in d["think"]:
                    print(json.dumps(x, ensure_ascii=False))
                print("\n== 新聊天 ==")
                for x in d["newChat"]:
                    print(json.dumps(x, ensure_ascii=False))
                print("\n== 输入框里的按钮 ==")
                for x in d["formButtons"]:
                    print(json.dumps(x, ensure_ascii=False))
                return


asyncio.run(main())
