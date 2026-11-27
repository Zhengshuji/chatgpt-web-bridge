"""比较: 同一个任务, 在"有仓库"和"无仓库"两个工作区, 提示词里的规则块。"""
import json
import pathlib
import sys

import requests

KEY = (pathlib.Path(__file__).resolve().parent / "data" / "key.txt").read_text().strip()
BASE = "http://127.0.0.1:8355"


def ask(cwd: str) -> str:
    body = {
        "model": "gpt-web", "stream": False,
        "messages": [
            {"role": "system", "content": "You are a coding agent. Current working directory: " + cwd},
            {"role": "user", "content": "把当前目录里有哪些文件告诉我"},
            {"role": "user", "content": f'<system-reminder>\nCurrent working directory: "{cwd}"\n</system-reminder>'},
        ],
        "tools": [{"type": "function", "function": {
            "name": "pwsh", "description": "shell",
            "parameters": {"type": "object", "properties": {"command": {}}, "required": ["command"]}}}],
    }
    r = requests.post(f"{BASE}/v1/chat/completions", json=body,
                      headers={"Authorization": f"Bearer {KEY}"}, timeout=600)
    d = r.json()
    msg = d["choices"][0]["message"]
    return (msg.get("content") or "") + json.dumps(msg.get("tool_calls") or [], ensure_ascii=False)


for cwd in (r"D:\Path\others\test", r"D:\Path\others\empty"):
    print(f"\n=== {cwd}")
    print(ask(cwd)[:220])
