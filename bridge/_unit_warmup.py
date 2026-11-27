"""验证: ①DSH 背景消息被挪到前面、任务紧贴尾部; ②两条快速通道。"""
import json
import pathlib
import sys

sys.path.insert(0, ".")
import server as s

# --- ① 用真实请求的 message 形状(来自 last_request.json 的历史记录, 这里重建)
msgs = [
    {"role": "system", "content": "You are an AI agent powered by DeepSeek Harness."},
    {"role": "user", "content": "写一个程序，实现0到10的阶乘"},
    {"role": "user", "content": "<system-reminder>\nThe following information describes your current session: ..."},
    {"role": "user", "content": "<system-reminder>\nA skill is a reusable set of task-specific instructions..."},
    {"role": "user", "content": "Time sampled while preparing turn 1, step 1: 2026-11-27T20:12:42+08:00"},
    {"role": "user", "content": "[MNEMON] Search Documents for substantial project records."},
    {"role": "user", "content": "MNEMON RUNTIME MEMORY SNAPSHOT\nEntry annotations default to ..."},
]
tools = [{"type": "function", "function": {"name": "pwsh", "description": "shell",
                                           "parameters": {"type": "object",
                                                          "properties": {"command": {}},
                                                          "required": ["command"]}}}]
text = s.flatten(msgs, tools)
body = text.split("[会话背景")[1]
tail_part = body[body.find("[user]\n写一个程序"):]
print("任务出现在背景之后:", "[会话背景" in text and text.rfind("写一个程序") > text.find("[会话背景"))
print("任务块之后紧跟的部分:", repr(text[text.rfind("写一个程序"):][:120]))
assert text.rfind("写一个程序") > text.find("[会话背景"), "任务必须在背景之后"
assert text.rfind("写一个程序") > text.find("MNEMON RUNTIME MEMORY SNAPSHOT"), "任务必须在记忆快照之后"

# --- ② 标题快速通道
title_body = {"model": "gpt-web", "stream": False, "messages": [
    {"role": "user", "content": 'Generate the session title from this JSON array of human messages: '
                                '[{"seq":8,"text":"写一个程序，实现0到10的阶乘"}]'}]}
got = s._fast_answer(title_body)
print("标题快速通道:", got)
assert got and got[1] == "title" and "阶乘" in got[0]

# --- ③ Mnemon 审查快速通道
rev_body = {"model": "gpt-web", "stream": False, "messages": [
    {"role": "system", "content": "You are Mnemon's conservative idle checkpoint reviewer. ..."},
    {"role": "user", "content": "review this"}]}
got2 = s._fast_answer(rev_body)
print("审查快速通道:", got2)
assert got2 and got2[1] == "reviewer"

# --- ④ 普通请求不能被误判
normal = {"model": "gpt-web", "stream": False, "messages": [
    {"role": "system", "content": "You are an agent."},
    {"role": "user", "content": "写一个程序"}]}
assert s._fast_answer(normal) is None
print("OK")
