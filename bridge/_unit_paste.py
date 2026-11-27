"""检测器单测: 拒答 / 只说话不动手 / 正常回答。"""
import sys

sys.path.insert(0, ".")
import server as s

cases = [
    ("lazy-code", '{"content":"def main():\\n print(1)","tool_calls":[]}', True),
    ("lazy-ack", '{"content":"已了解。后续严格使用单个合法 JSON 对象回复。","tool_calls":[]}', True),
    ("lazy-run", '{"content":"运行结果：5050","tool_calls":[]}', True),
    ("ok-answer", '{"content":"已经写好 demo/sum.py 并跑过，输出 5050。","tool_calls":[]}', False),
    ("ok-tool", '{"content":null,"tool_calls":[{"name":"write","arguments":{"path":"a.py","content":"x"}}]}', False),
    ("refusal", "当前会话没有连接你提供的执行器", True),
]

print(f"{'case':12} {'paste':6} {'refusal':8} {'tool_calls'}")
for name, text, expect_paste in cases:
    content, tcs = s.parse_reply(text)
    paste = s.looks_like_paste(content or text)
    refusal = s.looks_like_refusal(content or text)
    print(f"{name:12} {str(paste):6} {str(refusal):8} {bool(tcs)}")
    if name.startswith("lazy"):
        assert paste and not tcs, f"{name} 应被判为只说话不动手"
    if name.startswith("ok"):
        assert not paste or tcs, f"{name} 不应被误判"
print("OK: 检测器行为符合预期")
