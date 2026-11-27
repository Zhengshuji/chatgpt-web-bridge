import pathlib
p = pathlib.Path("server.py")
s = p.read_text(encoding="utf-8")

subs = [
    ('return err(f"网页操作失败: {send_err}")',
     'return err(f"网页操作失败: {send_err}", 502)'),
    ('return err("网页会话已达长度上限, 会话已失效; 重试将自动重建新会话", 429)',
     'return err("网页会话已达长度上限, 会话已失效; 请减少上下文或开新任务", 400, "context_length_exceeded")'),
    ('return err(f"采集失败: {e}")',
     'return err(f"采集失败: {e}", 502)'),
    ('return err("网页会话已达长度上限, 重建对话后仍失败; 请重试或减少上下文", 429)',
     'return err("网页会话已达长度上限, 重建对话后仍失败; 请减少上下文", 400, "context_length_exceeded")'),
]
for old, new in subs:
    n = s.count(old)
    print(n, "x", old[:48])
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
