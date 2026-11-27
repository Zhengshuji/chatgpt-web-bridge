import pathlib, re
p = pathlib.Path("server.py")
s = p.read_text(encoding="utf-8")

# 1) 会话文件路径
s = s.replace(
    'GW = Path(os.environ.get("GPTWEB_HOME") or (HOME / "data"))\nGW.mkdir(parents=True, exist_ok=True)',
    'GW = Path(os.environ.get("GPTWEB_HOME") or (HOME / "data"))\nGW.mkdir(parents=True, exist_ok=True)\n'
    'SESSIONS_FILE = GW / "sessions.json"\n'
    'LOG_MAX_BYTES = int(os.environ.get("GPTWEB_LOG_MAX", str(5 * 1024 * 1024)))\n'
    '# 分级超时: 页面安静这么久没有任何新字就判失败, 不再干等到总 deadline\n'
    'IDLE_TIMEOUT = float(os.environ.get("GPTWEB_IDLE_TIMEOUT", "60"))', 1)

# 2) 会话弹出/清空走带落盘的helper
s = s.replace('SESSIONS.pop(sess["sid"], None)', '_drop_session(sess["sid"])')
s = s.replace('SESSIONS.pop(old["sid"], None)', 'SESSIONS.pop(old["sid"], None)')
s = s.replace('        SESSIONS.clear()\n        log("CONVO-FULL',
              '        _clear_sessions()\n        log("CONVO-FULL')
p.write_text(s, encoding="utf-8")
print("done")
