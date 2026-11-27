import sys
sys.path.insert(0, ".")
import server as s

cases = [
    '{"content":null,"tool_calls":[{"name":"read_file","arguments":{"path":"D:\\\\a.txt"}}]}',
    '{"commentary":["x"],"instructions":[{"action":"write_file","path":"a.py","content":"print(1)"}]}',
    '好的，我先看看项目结构。',
    '```json\n{"content":"hi","tool_calls":[]}\n```',
    '{"role":"assistant","content":null,"tool_calls":[{"id":"c1","type":"function","function":{"name":"list_dir","arguments":"{\\"path\\":\\"D:\\\\x\\"}"}}]}',
]
for c in cases:
    print(repr(c[:45]), "->", s.parse_reply(c))
    print("   valid json:", s._extract_json_object(c) is not None)
