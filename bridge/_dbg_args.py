import asyncio
import sys
import time

sys.path.insert(0, ".")
import server as s

BAD = '{"content": null, "tool_calls": [{"name": "read", "arguments": {}}]}'
GOOD = '{"content": null, "tool_calls": [{"name": "read", "arguments": {"path": "a.txt"}}]}'
schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
seq = [BAD, GOOD]
state = {"i": 0}


async def fake_snap(base_count=-1):
    return {"text": seq[min(state["i"], 1)], "count": base_count + 1, "gen": False, "full": False}


async def fake_collect(base_count, base_text, deadline):
    print("  collect ->", seq[min(state["i"], 1)][:40], "| i =", state["i"])
    return seq[min(state["i"], 1)]


async def fake_send(p):
    print("  send ->", p[:40])
    state["i"] += 1


s.snapshot = fake_snap
s.collect_text = fake_collect
s.type_and_send = fake_send
s.log = lambda *a, **k: print("  LOG:", a[0][:60])
s.progress = lambda *a, **k: None

text, _, _ = asyncio.run(s.collect_json(0, "", time.monotonic() + 30, {"read"}, "SK", {"read": schema}))
print("final:", text[:60])
