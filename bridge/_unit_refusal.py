import sys
sys.path.insert(0, ".")
import server as s

cases = [
    ("拒答1", "当前会话没有连接你提供的本机 PowerShell 执行器，因此无法真实执行"),
    ("拒答2", "我没有访问本地文件系统的工具，不能执行命令"),
    ("拒答3", "请提供本地执行器的工具连接"),
    ("拒答4", "我不会伪造执行结果"),
    ("正常1", "我已经创建了 demo/squares.py 并运行通过，输出是 1,4,9"),
    ("正常2", '{"content":"完成了","tool_calls":[]}'),
    ("正常3", r"文件已写入 D:\Path\others\test\demo\squares.py"),
]
for name, t in cases:
    print(f"{name}: {s.looks_like_refusal(t)}  <- {t[:30]}")
