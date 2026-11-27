"""验证: ①命令表带类型 ②参数本地校验 ③缺参/错类型会触发重发。"""
import asyncio
import sys
import time

sys.path.insert(0, ".")
import server as s

TOOLS = [{"type": "function", "function": {
    "name": "read", "description": "读文件",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"},
                                  "limit": {"type": "integer"}},
                   "required": ["path"]}}}]

# ① 命令表格式
head, _ = s.build_tool_protocol(TOOLS)
line = [l for l in head.splitlines() if l.startswith("- read")][0]
print("命令表:", line)
assert "path*:string" in line and "limit?:integer" in line

# ② 校验函数
schema = TOOLS[0]["function"]["parameters"]
cases = [
    ({"path": r"D:\a.txt"}, ""),
    ({"path": r"D:\a.txt", "limit": 10}, ""),
    ({}, "缺少必填参数 path"),
    ({"path": 123}, "参数 path 需要 string"),
    ({"path": "x", "limit": "10"}, "参数 limit 需要 integer"),
    ({"path": "x", "extra": 1}, ""),          # 额外参数只记录, 不拦
    ("not-a-dict", "arguments 不是合法 JSON"),
]
for args, want in cases:
    got = s.validate_args(schema, args)
    ok = (not want and not got) or (want and got.startswith(want))
    print(f"{str(args)[:38]:40} -> {got!r}")
    assert ok, f"{args} 期望以 {want!r} 开头, 实际 {got!r}"

# ③ 缺必填会触发一次重发
BAD = '{"content": null, "tool_calls": [{"name": "read", "arguments": {}}]}'
GOOD = '{"content": null, "tool_calls": [{"name": "read", "arguments": {"path": "a.txt"}}]}'
seq = [BAD, GOOD]
state = {"i": 0}
sent = []


async def fake_snap(base_count: int = -1):
    return {"text": seq[min(state["i"], 1)], "count": base_count + 1, "gen": False, "full": False}


async def fake_collect(base_count, base_text, deadline):
    return seq[min(state["i"], 1)]


async def fake_send(p: str):
    sent.append(p)
    state["i"] += 1


s.snapshot = fake_snap
s.collect_text = fake_collect
s.type_and_send = fake_send
s.log = lambda *a, **k: None
s.progress = lambda *a, **k: None

text, _, _ = asyncio.run(s.collect_json(0, "", time.monotonic() + 30,
                                        {"read"}, "SK-ARGS", {"read": schema}))
print("重发次数:", len(sent), "| 最终:", text[:60])
assert len(sent) == 1 and "缺少必填参数 path" in sent[0]
assert "a.txt" in text
print("OK: 缺参被本地拦下并重发, 第二次通过")
