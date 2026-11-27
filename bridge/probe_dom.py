#!/usr/bin/env python3
"""探针 4：找回复节点到底挂在哪。从页面文本倒推 DOM 结构。"""
import asyncio
import json

import aiohttp
import websockets

CDP = "http://127.0.0.1:9225"

JS = r"""(() => {
  const hit = (needle) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if ((n.nodeValue || '').includes(needle)) return n;
    }
    return null;
  };
  const chain = (node, max) => {
    const out = [];
    let el = node.nodeType === 1 ? node : node.parentElement;
    while (el && out.length < max) {
      out.push({
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        cls: (el.className || '').toString().slice(0, 120),
        testid: el.getAttribute('data-testid') || '',
        attrs: [...el.attributes].map(a => a.name).filter(a => a.startsWith('data-')).join(','),
        textLen: (el.innerText || '').length
      });
      el = el.parentElement;
    }
    return out;
  };
  const n = hit('1+1=2');
  const u = hit('请只回答');
  return JSON.stringify({
    assistant: n ? chain(n, 9) : null,
    user: u ? chain(u, 9) : null,
    testids: [...new Set([...document.querySelectorAll('[data-testid]')].map(e => e.getAttribute('data-testid')))].slice(0, 40),
    dataMsg: [...document.querySelectorAll('[data-message-author-role]')].length
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
                print("=== assistant chain")
                for i, x in enumerate(d.get("assistant") or []):
                    print(f" {i} <{x['tag']}> id={x['id']!r} testid={x['testid']!r} data=[{x['attrs']}] len={x['textLen']}")
                    print(f"     class={x['cls']}")
                print("=== user chain")
                for i, x in enumerate(d.get("user") or []):
                    print(f" {i} <{x['tag']}> id={x['id']!r} testid={x['testid']!r} data=[{x['attrs']}] len={x['textLen']}")
                    print(f"     class={x['cls']}")
                print("=== testids on page:", d.get("testids"))
                return


asyncio.run(main())
