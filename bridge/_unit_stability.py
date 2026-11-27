"""验证: ①会话落盘/恢复 ②分级超时 ③日志轮转。"""
import asyncio
import json
import pathlib
import sys
import time

sys.path.insert(0, ".")
import server as s

# --- ① 会话落盘 / 恢复
s.SESSIONS.clear()
s._save_session("KEY1", ["a", "b"], "https://chatgpt.com/c/1")
assert s.SESSIONS_FILE.exists(), "sessions.json 应被创建"
rows = json.loads(s.SESSIONS_FILE.read_text(encoding="utf-8"))
print("落盘:", len(rows), "条 |", rows[0]["url"])
assert rows and rows[0]["url"].endswith("/c/1")

s.SESSIONS.clear()
s._load_sessions()
print("恢复后:", list(s.SESSIONS.values())[0]["url"])
assert len(s.SESSIONS) == 1 and list(s.SESSIONS.values())[0]["rounds"] == 0

# 轮次也持久化
sess = list(s.SESSIONS.values())[0]
s._remember(sess, "KEY1", ["a", "b", "c"], "https://chatgpt.com/c/1")
s.SESSIONS.clear()
s._load_sessions()
assert list(s.SESSIONS.values())[0]["rounds"] == 1, "轮次应落盘"
print("轮次恢复:", list(s.SESSIONS.values())[0]["rounds"])

# 超 12 小时的旧会话不恢复
rows[0]["ts"] = time.time() - 13 * 3600
s.SESSIONS_FILE.write_text(json.dumps(rows), encoding="utf-8")
s.SESSIONS.clear()
s._load_sessions()
assert not s.SESSIONS, "过期会话不该恢复"
print("过期会话已丢弃: ok")

# --- ② 分级超时: 页面一直没新内容 -> UpstreamIdle
async def stall(*a, **k):
    return {"text": "旧内容", "count": 1, "gen": True, "full": False}


s.snapshot = stall
s.IDLE_TIMEOUT = 0.5
try:
    asyncio.run(s.collect_text(0, "旧内容", time.monotonic() + 10))
    print("!! 应该抛 UpstreamIdle")
    raise SystemExit(1)
except s.UpstreamIdle as e:
    print("分级超时触发:", e)

# --- ③ 日志轮转
logf = s.LOG_FILE
s.LOG_MAX_BYTES = 200
logf.write_text("x" * 500, encoding="utf-8")
s.log("轮转测试")
rotated = pathlib.Path(str(logf) + ".1")
print("轮转文件存在:", rotated.exists(), "| 新文件大小:", logf.stat().st_size)
assert rotated.exists()
s.LOG_MAX_BYTES = 5 * 1024 * 1024
logf.unlink(missing_ok=True)
rotated.unlink(missing_ok=True)
s.SESSIONS.clear()
s._save_sessions()
print("ALL OK")
