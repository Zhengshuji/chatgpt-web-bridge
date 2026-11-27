"""验证: ①背景块去重 ②工具结果压缩 ③错误分类 ④长消息识别。"""
import sys

sys.path.insert(0, ".")
import server as s

# --- ① 同一份背景块只留最后一次
boiler = {"role": "user", "content": "MNEMON RUNTIME MEMORY SNAPSHOT\n" + "条目 " * 20}
msgs = [
    {"role": "user", "content": "写个脚本"},
    dict(boiler),
    {"role": "user", "content": "Time sampled while preparing turn 1, step 1: x"},
    dict(boiler),  # 一模一样, 应被折叠
]
tools = [{"type": "function", "function": {"name": "pwsh", "description": "d",
                                           "parameters": {"type": "object",
                                                          "properties": {"command": {}},
                                                          "required": ["command"]}}}]
text = s.flatten(msgs, tools)
n = text.count("MNEMON RUNTIME MEMORY SNAPSHOT")
print("背景块出现次数:", n)
assert n == 1, f"应只保留 1 份, 实际 {n}"

# --- ② 工具结果压缩
big = "line0\n" + "same\n" * 400 + "lineN"
out = s._squeeze(big, 500)
print("压缩前:", len(big), "压缩后:", len(out))
assert len(out) <= 700 and "省略" in out or "重复" in out

short = "exit=0\n5050"
assert s._squeeze(short, 500) == short
print("短结果不压缩: ok")

# --- ③ 错误分类
r = s.err("限流", 429)
print("429 type:", r.text[:120])
assert "rate_limit_exceeded" in r.text
r2 = s.err("太长", 400, "context_length_exceeded")
assert "context_length_exceeded" in r2.text
r3 = s.err("x", 502)
assert "upstream_error" in r3.text
r4 = s.err("x", 401)
assert "authentication_error" in r4.text
print("错误分类: ok")

# --- ④ 长消息/限流识别
assert s.is_too_long_text("The message you submitted was too long")
assert s.is_too_long_text("内容过长，请缩短")
assert not s.is_too_long_text("正常的回复")
assert s.is_limit_text("You've hit your limit")
assert not s.is_limit_text("好的")
print("识别: ok")
print("ALL OK")
