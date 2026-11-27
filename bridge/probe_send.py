#!/usr/bin/env python3
"""探针 2：往输入框塞一段字，看看发送按钮到底长什么样。"""
import asyncio
import json

import aiohttp
import websockets

CDP = "http://127.0.0.1:9225"

INSERT = r"""(() => {
  const el = document.querySelector('#prompt-textarea') || document.querySelector('div[contenteditable="true"][role="textbox"]');
  if (!el) return JSON.stringify({err:'no composer'});
  el.focus();
  document.execCommand('insertText', false, '测试一下');
  return JSON.stringify({len: el.innerText.length});
})()"""

DUMP = r"""(() => {
  const btns = [...document.querySelectorAll('button')].map(b => ({
    t: (b.innerText||'').trim().slice(0,20),
    tid: b.getAttribute('data-testid') || '',
    aria: b.getAttribute('aria-label') || '',
    id: b.id || '',
    dis: b.disabled,
    cls: (b.className||'').toString().slice(0,60)
  })).filter(b => b.tid || b.aria || b.id || /send|submit/i.test(b.cls));
  const form = document.querySelector('form');
  return JSON.stringify({buttons: btns, hasForm: !!form, formHTML: form ? form.outerHTML.slice(0,600) : ''});
})()"""


async def evaluate(ws, expr, i):
    await ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True,
                                         "awaitPromise": True}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == i:
            res = msg.get("result", {}).get("result", {})
            raw = res.get("value")
            return json.loads(raw) if raw else {"err": json.dumps(res)[:300]}


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{CDP}/json/list") as r:
            tabs = await r.json()
    tab = next(t for t in tabs if t.get("type") == "page" and "chatgpt.com" in t.get("url", ""))
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024) as ws:
        print("INSERT:", await evaluate(ws, INSERT, 1))
        await asyncio.sleep(1.5)
        d = await evaluate(ws, DUMP, 2)
        print("hasForm:", d.get("hasForm"))
        for b in d.get("buttons", []):
            print("  BTN", json.dumps(b, ensure_ascii=False))
        print("form:", d.get("formHTML", "")[:400])


asyncio.run(main())
