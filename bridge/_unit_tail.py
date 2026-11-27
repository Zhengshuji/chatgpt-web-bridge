"""验证：①尾部原样重贴本轮任务 ②最终回答附改动清单。"""
import sys

sys.path.insert(0, ".")
import server as s

TOOLS = [{"type": "function", "function": {
    "name": "pwsh", "description": "shell",
    "parameters": {"type": "object", "properties": {"command": {}}, "required": ["command"]}}}]

msgs = [
    {"role": "system", "content": "You are a DSH agent with a long system prompt."},
    {"role": "user", "content": "检查本地 conda 环境，写一个二连杆拉格朗日推导程序"},
    {"role": "user", "content": "<system-reminder>\nCurrent working directory: \"D:\\\\Path\\\\others\\\\test\"\n</system-reminder>"},
    {"role": "user", "content": "MNEMON RUNTIME MEMORY SNAPSHOT\n条目若干"},
]
text = s.flatten(msgs, TOOLS)
tail = text[text.rfind("[本轮任务"):]
print("=== 尾部 ===")
print(tail[:200])
assert "本轮任务" in text and text.rstrip().endswith("推导程序"), "任务应原样出现在最末"
assert text.rfind("本轮任务") > text.rfind("[回到正常对话]"), "任务应在尾部提醒之后"

# 已有 tool 结果时不该再贴任务（避免带偏）
msgs2 = msgs[:2] + [{"role": "tool", "name": "pwsh", "content": "exit=0"}]
text2 = s.flatten(msgs2, TOOLS)
assert "[本轮任务" not in text2, "最后一条不是用户消息时不应重贴"
print("最后一条是工具结果: 不重贴 ok")

# ② 改动清单
s.CFG.__dict__  # 只读
out = s._decorate("程序已生成。", msgs)
print("\n=== 最终回答 ===")
print(out[:300])
assert "本地核对" in out and "demo" in out or "bridge" in out
print("\nALL OK")
