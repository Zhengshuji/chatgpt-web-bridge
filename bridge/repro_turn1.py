"""复现 DSH 新会话的第一轮: 任务在最前, 后面跟着四坨提醒, 看模型还会不会反问"任务是什么"。"""
import json
import pathlib
import sys

import requests

KEY = (pathlib.Path(__file__).resolve().parent / "data" / "key.txt").read_text().strip()
BASE = "http://127.0.0.1:8355"

TOOLS = [
    {"type": "function", "function": {
        "name": "pwsh", "description": "在本地 PowerShell 里执行命令",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read", "description": "读文件",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write", "description": "写文件",
        "parameters": {"type": "object", "properties": {
            "file_path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["file_path", "content"]}}},
    {"type": "function", "function": {
        "name": "glob", "description": "按通配符找文件",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}},
                       "required": ["pattern"]}}},
]

MESSAGES = [
    {"role": "system", "content":
        "You are an AI agent powered by DeepSeek Harness. You are a coding agent powered by the gpt-web model. "
        "Current working directory: D:\\Path\\others\\empty. Complete the user's task by using tools."},
    {"role": "user", "content": "写一个程序，实现0到10的阶乘"},
    {"role": "user", "content":
        "<system-reminder>\nThe following information describes your current session:\n\n"
        "Current working directory: \"D:\\\\Path\\\\others\\\\empty\"\n"
        "Current DSH file policy: danger-full-access.\n</system-reminder>"},
    {"role": "user", "content":
        "<system-reminder>\nA skill is a reusable set of task-specific instructions. "
        "The following skills are available in this session:\n<available_skills>\n- office: ...\n</available_skills>\n"
        "</system-reminder>"},
    {"role": "user", "content":
        "Time sampled while preparing turn 1, step 1: 2026-11-27T20:12:42+08:00[Asia/Shanghai]\n"
        "Browser time zone for this request: Asia/Shanghai. Interpret otherwise-unqualified dates and times "
        "in this zone.\nElapsed since the preceding model-visible message: unavailable."},
    {"role": "user", "content":
        "[MNEMON] Search Documents for substantial project records. For mnemon_runtime_memory: Capture only new, "
        "explicit, durable user-authored identity/preferences or compact rules needed for future work."},
    {"role": "user", "content":
        "MNEMON RUNTIME MEMORY SNAPSHOT\nEntry annotations default to importance=normal and ages=0d.\n\n"
        "USER.md (user profile)\n<empty>\n\nMEMORY.md (working reference)\n<empty>"},
]


def main() -> int:
    body = {"model": "gpt-web", "stream": True, "messages": MESSAGES, "tools": TOOLS}
    r = requests.post(f"{BASE}/v1/chat/completions", json=body,
                      headers={"Authorization": f"Bearer {KEY}"}, timeout=600, stream=True)
    print("HTTP", r.status_code)
    text = ""
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            d = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for ch in d.get("choices", []):
            dl = ch.get("delta") or {}
            if dl.get("content"):
                text += dl["content"]
            if dl.get("tool_calls"):
                print("tool_call:", json.dumps(dl["tool_calls"], ensure_ascii=False)[:300])
    print("content:", text[:300] or "(空)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
