#!/usr/bin/env python3
"""复现 DSH 的请求形状: 长系统提示 + 多个工具 + 一个"写脚本"的任务。
用来看模型会不会真的给 tool_calls, 而不是把代码写在 content 里。"""
import json
import pathlib
import sys

import requests

KEY = (pathlib.Path(__file__).resolve().parent / "data" / "key.txt").read_text().strip()
BASE = "http://127.0.0.1:8355"

TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "在本地 shell 里执行命令并返回输出",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "timeout": {"type": "number"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read", "description": "读取文件内容(带行号)",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "number"}, "limit": {"type": "number"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write", "description": "写入文件(覆盖)",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit", "description": "编辑文件的片段",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "glob", "description": "按通配符找文件",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}},
                       "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "grep", "description": "按正则搜索文件内容",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}},
                       "required": ["pattern"]}}},
]

SYSTEM = (
    "你是一个编码代理，工作目录是 D:\\Path\\others\\test\\empty（当前为空）。\n"
    "完成任务时要用工具真实操作文件，不要只在回答里给代码。\n"
    "回答用中文。"
)


def main() -> int:
    task = sys.argv[1] if len(sys.argv) > 1 else "写一个python脚本实现0到100的求和"
    body = {"model": "gpt-web", "stream": False,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": task}],
            "tools": TOOLS}
    r = requests.post(f"{BASE}/v1/chat/completions", json=body,
                      headers={"Authorization": f"Bearer {KEY}"}, timeout=900)
    print("HTTP", r.status_code)
    if r.status_code != 200:
        print(r.text[:500])
        return 1
    d = r.json()
    msg = d["choices"][0]["message"]
    print("finish:", d["choices"][0]["finish_reason"])
    print("content:", (msg.get("content") or "")[:400])
    for tc in msg.get("tool_calls") or []:
        print("tool_call:", tc["function"]["name"], tc["function"]["arguments"][:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
