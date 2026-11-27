"""把 last_request.json 浓缩成可读摘要: 谁发的、多少工具、每条消息的开头。"""
import json
import pathlib

p = pathlib.Path(r"D:\Path\others\test\bridge\data\last_request.json")
print("存在:", p.exists(), "大小:", p.stat().st_size if p.exists() else 0)
if not p.exists():
    raise SystemExit

d = json.loads(p.read_text(encoding="utf-8"))
print("model:", d.get("model"), "stream:", d.get("stream"), "keys:", list(d.keys()))
tools = d.get("tools") or []
print("tools:", len(tools))
print("tool names:", [t.get("function", {}).get("name") for t in tools])
msgs = d.get("messages") or []
print("messages:", len(msgs))
for i, m in enumerate(msgs):
    c = m.get("content")
    if isinstance(c, list):
        c = " ".join(str(x.get("text", ""))[:200] for x in c if isinstance(x, dict))
    c = (c or "")
    extra = f" tool_calls={len(m['tool_calls'])}" if m.get("tool_calls") else ""
    print(f"\n[{i}] role={m.get('role')} len={len(str(c))}{extra}")
    print("    " + str(c)[:600].replace("\n", " ⏎ "))
