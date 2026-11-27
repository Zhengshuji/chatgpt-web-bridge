#!/usr/bin/env python3
"""最小客户端：打本地 bridge，验证 文本 / 工具调用 / 流式。"""
import argparse
import json
import sys
from pathlib import Path

import requests

KEY = (Path(__file__).resolve().parent / "data" / "key.txt").read_text().strip()
BASE = "http://127.0.0.1:8355"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取本地文件的完整内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件绝对路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录下的文件名",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default="用一句话说明你现在是不是真的能读本地文件。")
    ap.add_argument("--tools", action="store_true")
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--followup", action="store_true", help="工具结果回填的第二轮")
    ap.add_argument("--model", default="gpt-web", help="模型 slug 或 gpt-web(auto)")
    args = ap.parse_args()

    messages = [
        {"role": "system", "content": "你是一个本地编码代理，所有回答使用中文。"},
        {"role": "user", "content": args.prompt},
    ]
    if args.followup:
        messages += [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_test1", "type": "function",
                "function": {"name": "read_file",
                             "arguments": json.dumps({"path": r"D:\Path\others\test\README.md"})}}]},
            {"role": "tool", "tool_call_id": "call_test1", "name": "read_file",
             "content": "文件不存在（测试用假结果）"},
        ]

    body = {"model": args.model, "messages": messages, "stream": args.stream}
    if args.tools:
        body["tools"] = TOOLS

    url = BASE + "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    if not args.stream:
        r = requests.post(url, json=body, headers=headers, timeout=420)
        print("HTTP", r.status_code)
        print(json.dumps(r.json(), ensure_ascii=False, indent=1)[:4000])
        return 0 if r.ok else 1

    with requests.post(url, json=body, headers=headers, timeout=420, stream=True) as r:
        print("HTTP", r.status_code)
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
