import json
import pathlib

p = pathlib.Path(r"D:\Path\others\test\bridge\data\last_request.json")
d = json.loads(p.read_text(encoding="utf-8"))
print("model:", d.get("model"), "stream:", d.get("stream"), "tools:", len(d.get("tools") or []))
for i, m in enumerate(d.get("messages") or []):
    c = m.get("content")
    if isinstance(c, list):
        c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    c = str(c or "")
    print(f"\n[{i}] {m.get('role')} len={len(c)}")
    print("   head:", c[:160].replace("\n", " ⏎ "))
    print("   tail:", c[-160:].replace("\n", " ⏎ "))
