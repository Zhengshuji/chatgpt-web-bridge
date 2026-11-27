import json
import pathlib
import time

import requests

KEY = pathlib.Path("data/key.txt").read_text().strip()
H = {"Authorization": f"Bearer {KEY}"}

t0 = time.time()
r = requests.post("http://127.0.0.1:8355/v1/chat/completions", headers=H, timeout=60, json={
    "model": "gpt-web", "stream": False,
    "messages": [{"role": "user", "content":
                  'Generate the session title from this JSON array of human messages: '
                  '[{"seq":8,"text":"写一个程序，实现0到10的阶乘"}]'}]})
print("title:", r.status_code, repr(r.json()["choices"][0]["message"]["content"]),
      f"{time.time()-t0:.2f}s")

t0 = time.time()
r = requests.post("http://127.0.0.1:8355/v1/chat/completions", headers=H, timeout=60, json={
    "model": "gpt-web", "stream": True,
    "tools": [{"type": "function", "function": {"name": "mnemon_document_create",
                                                "parameters": {"type": "object", "properties": {}}}}],
    "messages": [{"role": "system", "content": "You are Mnemon's conservative idle checkpoint reviewer. Review..."},
                 {"role": "user", "content": "review"}]})
print("reviewer:", r.status_code, f"{time.time()-t0:.2f}s")
print(r.text[:260])
