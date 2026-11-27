"""验证本地规则注入与"别再重试"提示。"""
import sys

sys.path.insert(0, ".")
import server as s

# ① 本地规则注入
s.POLICY = "仓库 origin 已建好：git@github.com:me/proj.git；分支 main；每轮结束强推；不要询问仓库名。"
tools = [{"type": "function", "function": {"name": "pwsh", "description": "shell",
                                           "parameters": {"type": "object",
                                                          "properties": {"command": {}},
                                                          "required": ["command"]}}}]
text = s.flatten([{"role": "user", "content": "写个脚本"}], tools)
print("规则在开头:", text.find("本地已定死的规则") >= 0)
print("规则排在命令表之后、任务之前:", text.find("本地已定死的规则") > text.find("[先说一下我这边的情况]"),
      text.find("本地已定死的规则") < text.find("写个脚本"))
assert s.POLICY in text

# ② 环境失败提示
env_err = ("exit=1\n[sandbox: file access denied under workspace-write mode]\n"
           "Error: windows-acl workspace grant failed: SetNamedSecurityInfoW failed (Win32 5)")
msgs = [{"role": "user", "content": "跑一下"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "pwsh", "arguments": "{\"command\":\"python a.py\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "pwsh", "content": env_err}]
hint = s._history_hint(msgs)
print("环境失败提示:", hint[:60])
assert "环境权限错误" in hint

# ③ 重复失败提示
same = "exit=1\nModuleNotFoundError: No module named 'foo'"
msgs2 = [{"role": "user", "content": "跑一下"},
         {"role": "tool", "name": "pwsh", "content": same},
         {"role": "tool", "name": "pwsh", "content": same}]
hint2 = s._history_hint(msgs2)
print("重复失败提示:", hint2[:60])
assert "重复失败" in hint2

# ④ 正常工具结果不该被提示
msgs3 = [{"role": "user", "content": "跑一下"},
         {"role": "tool", "name": "pwsh", "content": "exit=0\n5050"}]
assert s._history_hint(msgs3) == ""
print("OK: 正常结果不触发提示")
