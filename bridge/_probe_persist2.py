"""会话持久化两步法。用法:
    python _probe_persist.py 1    # 发第一个请求, 应 full, 并写下 sessions.json
    (重启桥)
    python _probe_persist.py 2    # 发第二个请求, 应 delta 且 url 相同
"""
import json
import pathlib
import sys

import requests

KEY = pathlib.Path("data/key.txt").read_text(encoding="utf-8").strip()
H = {"Authorization": f"Bearer {KEY}"}
TOOLS = [{"type": "function", "function": {
    "name": "list_dir", "description": "列目录",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]

base = [{"role": "system", "content": "agent"},
        {"role": "user", "content": r"把 D:\Path\others\test\demo 目录列一下"}]
msgs2 = base + [
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "list_dir", "arguments": json.dumps({"path": "demo"})}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "list_dir", "content": "squares.py\nREADME.md"},
]

step = sys.argv[1] if len(sys.argv) > 1 else "1"
body = {"model": "gpt-web", "stream": False, "tools": TOOLS,
        "messages": base if step == "1" else msgs2}
r = requests.post("http://127.0.0.1:8355/v1/chat/completions", json=body,
                  headers=H, timeout=300)
d = r.json()
state = pathlib.Path("data/_persist_state.json")
info = {"mode": d.get("session_mode"), "url": d.get("conversation_url")}
print(json.dumps(info, ensure_ascii=False))
if step == "1":
    state.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
else:
    prev = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
    same = prev.get("url") and prev.get("url") == info["url"]
    ok = info["mode"] == "delta" and same
    print("RESULT:", "OK 持久化生效（重启后复用同一对话）" if ok else
          f"FAIL 没复用（之前 {prev.get('url')}）")
    sys.exit(0 if ok else 1)
