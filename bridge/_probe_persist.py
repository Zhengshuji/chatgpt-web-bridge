"""端到端验证会话持久化: 请求①开新对话 → 重启桥 → 请求②应复用(delta)而不是重开。"""
import json
import pathlib
import subprocess
import sys
import time

import requests

KEY = pathlib.Path("data/key.txt").read_text(encoding="utf-8").strip()
H = {"Authorization": f"Bearer {KEY}"}
TOOLS = [{"type": "function", "function": {
    "name": "list_dir", "description": "列目录",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]


def ask(messages):
    body = {"model": "gpt-web", "stream": False, "messages": messages, "tools": TOOLS}
    r = requests.post("http://127.0.0.1:8355/v1/chat/completions", json=body,
                      headers=H, timeout=300)
    d = r.json()
    return d.get("session_mode"), d.get("conversation_url"), d["choices"][0]["message"]


base = [{"role": "system", "content": "agent"},
        {"role": "user", "content": r"把 D:\Path\others\test\demo 目录列一下"}]

mode1, url1, msg = ask(base)
print("请求①:", mode1, url1)

# 重启桥（模拟计划任务自愈）
subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*server.py*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], timeout=60)
time.sleep(3)
subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "service.ps1"],
               timeout=180)
for _ in range(30):
    try:
        requests.get("http://127.0.0.1:8355/health", timeout=2)
        break
    except requests.RequestException:
        time.sleep(1)
print("桥已重启")

# 请求②: 在①的基础上多一条 assistant(tool_calls) + tool 结果 —— 指纹链是①的前缀扩展
msgs = base + [
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "list_dir", "arguments": json.dumps({"path": "demo"})}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "list_dir", "content": "squares.py\nREADME.md"},
]
mode2, url2, msg2 = ask(msgs)
print("请求②:", mode2, url2)
print("同一个网页对话:", url1 == url2)
print("RESULT:", "OK 持久化生效" if mode2 == "delta" and url1 == url2 else "FAIL 没复用")
sys.exit(0 if mode2 == "delta" else 1)
