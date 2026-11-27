import asyncio, json, aiohttp, websockets
async def main():
    async with aiohttp.ClientSession() as s:
        async with s.get("http://127.0.0.1:9225/json/list") as r:
            tabs = await r.json()
    tab = next(t for t in tabs if t.get("type")=="page" and "chatgpt.com" in t.get("url",""))
    js = "(() => { const m=document.querySelectorAll('[data-dil-response]'); return JSON.stringify({n:m.length, texts:[...m].map(x=>x.innerText)}); })()"
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=20*1024*1024) as ws:
        await ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":js,"returnByValue":True,"awaitPromise":True}}))
        while True:
            msg=json.loads(await ws.recv())
            if msg.get("id")==1:
                d=json.loads(msg["result"]["result"]["value"])
                for i,t in enumerate(d["texts"]): print(f"--- reply[{i}] ---"); print(t)
                return
asyncio.run(main())
