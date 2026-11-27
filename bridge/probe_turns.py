#!/usr/bin/env python3
"""探针 5：枚举对话消息块，确认 role 方向的取值。"""
import asyncio
import json

import aiohttp
import websockets

CDP = "http://127.0.0.1:9225"

JS = r"""(() => {
  const nodes = [...document.querySelectorAll('[data-dil-message-id]')];
  return JSON.stringify({
    n: nodes.length,
    items: nodes.map(n => ({
      id: n.getAttribute('data-dil-message-id'),
      dir: n.getAttribute('data-d-direction'),
      resp: n.hasAttribute('data-dil-response'),
      text: (n.innerText || '').replace(/\s+/g, ' ').slice(0, 60),
      markdownRoot: !!n.querySelector('[data-markdown-text-style]')
    }))
  });
})()"""


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{CDP}/json/list") as r:
            tabs = await r.json()
    tab = next(t for t in tabs if t.get("type") == "page" and "chatgpt.com" in t.get("url", ""))
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": JS, "returnByValue": True,
                                             "awaitPromise": True}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                d = json.loads(msg["result"]["result"]["value"])
                print("messages:", d["n"])
                for x in d["items"]:
                    print(f"  dir={x['dir']!r} resp={x['resp']} md={x['markdownRoot']} id={x['id']} :: {x['text']}")
                return


asyncio.run(main())
