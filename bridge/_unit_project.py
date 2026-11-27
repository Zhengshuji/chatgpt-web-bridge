"""验证: 有仓库的项目给地址、没仓库的项目明说别建。"""
import sys

sys.path.insert(0, ".")
import server as s

tools = [{"type": "function", "function": {
    "name": "pwsh", "description": "shell",
    "parameters": {"type": "object", "properties": {"command": {}}, "required": ["command"]}}}]


def mk(cwd: str) -> list:
    return [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "写一个脚本"},
        {"role": "user", "content": f'<system-reminder>\nCurrent working directory: "{cwd}"\n</system-reminder>'},
    ]


# ① 有 remote 的仓库
text = s.flatten(mk(r"D:\Path\others\test"), tools)
print("=== 有仓库 ===")
seg = text[text.find("[本地已定死的规则"):text.find("[You are") if "[You are" in text else text.find("[system]")]
print(seg.strip())
assert "github.com/Zhengshuji/chatgpt-web-bridge" in text
assert "没有 git 仓库" not in text

# ② 没有仓库的目录
text2 = s.flatten(mk(r"D:\Path\others\empty"), tools)
print("\n=== 没仓库 ===")
seg2 = text2[text2.find("[本地已定死的规则"):text2.find("[system]")]
print(seg2.strip())
assert "没有 git 仓库" in text2 and "不要 push" in text2

# ③ 目录不存在
text3 = s.flatten(mk(r"D:\Path\others\nope-nope"), tools)
assert "不存在或不可读" in text3
print("\n=== 不存在的目录 ===\nok")

# ④ 没给 cwd 时不注入
text4 = s.flatten([{"role": "user", "content": "写个脚本"}], tools)
assert "[本地已定死的规则" not in text4
print("=== 无 cwd：不注入 ===\nok")
print("\nALL OK")
