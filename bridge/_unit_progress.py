"""验证进度文件与幂等守卫。"""
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, ".")
import server as s

# --- ① 进度文件写进工作区的 .agent/
cwd = r"D:\Path\others\test"
s.progress(cwd=cwd, t0=time.time() - 3, round=2, phase="等待网页回复",
           event="已发送增量", cmd="pwsh", conv="https://chatgpt.com/c/x")
p = pathlib.Path(cwd) / ".agent" / "PROGRESS.md"
print("进度文件存在:", p.exists())
print(p.read_text(encoding="utf-8"))
assert p.exists() and "等待网页回复" in p.read_text(encoding="utf-8")

# --- ② 幂等守卫: 同一条调用第 3 次被拦
tool_json = '{"content": null, "tool_calls": [{"name": "pwsh", "arguments": {"command": "python a.py"}}]}'
REPLIES = [tool_json] * 6
state = {"i": 0}
sent: list[str] = []


async def fake_snapshot(base_count: int = -1):
    return {"text": REPLIES[min(state["i"], len(REPLIES) - 1)],
            "count": base_count + 1, "gen": False, "full": False}


async def fake_send(prompt: str):
    sent.append(prompt)
    state["i"] += 1


s.snapshot = fake_snapshot
s.type_and_send = fake_send
s.collect_text_orig = s.collect_text


async def fast_collect(base_count, base_text, deadline):
    return REPLIES[min(state["i"], len(REPLIES) - 1)]


s.collect_text = fast_collect
s.log = lambda *a, **k: None
s.progress = lambda *a, **k: None

for round_no in (1, 2, 3):
    state["i"] = round_no - 1
    sent.clear()
    asyncio.run(s.collect_json(0, "", time.monotonic() + 30, {"pwsh"}, "SKEY-TEST"))
    print(f"第 {round_no} 次请求: 催发 {len(sent)} 次")
    if round_no == 3:
        for m in sent:
            print("   ", m[:52])
        assert sent, "第 3 次应该被拦下并催换思路"
        assert any("之前已经发过两次" in m or "重复调用被拦下" in m or "别再发同一条命令" in m
                   for m in sent)
    else:
        assert not sent, f"第 {round_no} 次不该被拦"
print("OK: 幂等守卫生效（前两次放行, 第三次拦下）")
