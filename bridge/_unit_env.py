"""验证: ①本机环境路径注入 ②conda 失败时的本地提示。"""
import sys

sys.path.insert(0, ".")
import server as s

TOOLS = [{"type": "function", "function": {
    "name": "pwsh", "description": "shell",
    "parameters": {"type": "object", "properties": {"command": {}}, "required": ["command"]}}}]

# ① 环境路径
print("ENV_FACTS 行数:", len(s.ENV_FACTS.splitlines()))
text = s.flatten([{"role": "user", "content": "看下环境"}], TOOLS)
seg = text[text.find("[本机环境"):][:200]
print(seg)
assert "conda" in text and "绝对路径" in text

# ② conda 失败提示
msgs = [
    {"role": "user", "content": "检查 conda 环境"},
    {"role": "tool", "name": "pwsh",
     "content": "exit=1\nCondaError: plugin initialization failed (conda-libmamba-solver)"},
]
hint = s._history_hint(msgs)
print("\n提示:", hint[:90])
assert "conda" in hint and "绝对路径" in hint

# ③ 正常结果不误报
msgs2 = [{"role": "user", "content": "x"},
         {"role": "tool", "name": "pwsh", "content": "exit=0\nPython 3.12.7"}]
assert s._history_hint(msgs2) == ""
print("\nALL OK")
