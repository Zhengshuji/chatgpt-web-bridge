"""发一个带 cwd 的请求, 期间/之后看 .agent/PROGRESS.md 有没有跟上。"""
import pathlib
import threading
import time

import requests

KEY = pathlib.Path("data/key.txt").read_text(encoding="utf-8").strip()
CWD = r"D:\Path\others\test"
PROG = pathlib.Path(CWD) / ".agent" / "PROGRESS.md"
if PROG.exists():
    PROG.unlink()

body = {
    "model": "gpt-web", "stream": False,
    "messages": [
        {"role": "system", "content": "agent"},
        {"role": "user", "content": r"把 D:\Path\others\test\demo 目录列一下"},
        {"role": "user", "content": f'<system-reminder>\nCurrent working directory: "{CWD}"\n</system-reminder>'},
    ],
    "tools": [{"type": "function", "function": {
        "name": "pwsh", "description": "shell",
        "parameters": {"type": "object", "properties": {"command": {}}, "required": ["command"]}}}],
}

seen = []


def watch() -> None:
    for _ in range(40):
        if PROG.exists():
            seen.append(PROG.read_text(encoding="utf-8"))
        time.sleep(0.5)


th = threading.Thread(target=watch, daemon=True)
th.start()
r = requests.post("http://127.0.0.1:8355/v1/chat/completions", json=body,
                  headers={"Authorization": f"Bearer {KEY}"}, timeout=300)
th.join(timeout=2)
print("HTTP", r.status_code)
print("请求过程中看到进度文件:", len({s for s in seen}), "个不同版本")
if PROG.exists():
    print("--- 最终内容 ---")
    print(PROG.read_text(encoding="utf-8"))
else:
    print("!! 没有生成进度文件")
