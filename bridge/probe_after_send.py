#!/usr/bin/env python3
"""探针 3：真的发一条消息，边发边看 DOM 结构（发送按钮 / 停止按钮 / 回复节点）。"""
import asyncio
import json

import aiohttp
import websockets

CDP = "http://127.0.0.1:9225"
TEXT = "请只回答：4"

SEND = r"""(() => {
  const el = document.querySelector('#prompt-textarea') || document.querySelector('div[contenteditable="true"][role="textbox"]');
  if (!el) return JSON.stringify({err:'no composer'});
  el.focus();
  document.execCommand('insertText', false, %s);
  const btn = document.querySelector('button[aria-label="发送"], button[aria-label*="Send"], [data-testid="send-button"]');
  if (!btn) return JSON.stringify({err:'no send button', len: el.innerText.length});
  btn.click();
  return JSON.stringify({ok:true, len: el.innerText.length});
})()""" % json.dumps(TEXT)

WATCH = r"""(() => {
  const btns = [...document.querySelectorAll('button')]
    .filter(b => /停止|stop/i.test((b.getAttribute('aria-label')||'') + (b.getAttribute('data-testid')||'')))
    .map(b => b.getAttribute('aria-label') || b.getAttribute('data-testid'));
  const roles = document.querySelectorAll('[data-message-author-role]').length;
  const turns = document.querySelectorAll('[data-testid^="conversation-turn"]').length;
  const arts = document.querySelectorAll('article').length;
  const md = document.querySelectorAll('.markdown').length;
  const body = (document.body.innerText || '');
  return JSON.stringify({stop: btns, roles, turns, arts, md, tail: body.slice(-160), url: location.pathname});
})()"""


async def ev(ws, expr, i):
    await ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True,
                                         "awaitPromise": True}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == i:
            res = msg.get("result", {}).get("result", {})
            raw = res.get("value")
            return json.loads(raw) if raw else {"err": json.dumps(res)[:200]}


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{CDP}/json/list") as r:
            tabs = await r.json()
    tab = next(t for t in tabs if t.get("type") == "page" and "chatgpt.com" in t.get("url", ""))
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024) as ws:
        print("SEND:", await ev(ws, SEND, 1))
        for i in range(2, 26):
            await asyncio.sleep(1)
            d = await ev(ws, WATCH, i)
            print(f"{i-1:2d}s stop={d.get('stop')} roles={d.get('roles')} turns={d.get('turns')} "
                  f"arts={d.get('arts')} md={d.get('md')} {d.get('url')}")
            print("      tail:", (d.get("tail") or "").replace("\n", " | ")[:150])


asyncio.run(main())
