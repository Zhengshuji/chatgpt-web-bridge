"""本地工具库 —— 全部落在 Windows PowerShell 上。

设计约束（和 DeepSeek Harness 对齐）：
- 每个工具都是 OpenAI function schema，名字/参数扁平，和 DSH 的 bash/read/write 那套一个路子；
- 执行结果一律返回**字符串**，由调用方塞进 role="tool" 的消息里；
- 所有路径都限制在工作区内，避免网页那头写出界。

WORKSPACE 由 AGENT_WORKSPACE 指定，默认是本文件的上两级（仓库根）。
"""
import json
import os
import re
import subprocess
from pathlib import Path

WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE") or Path(__file__).resolve().parents[1]).resolve()
MAX_OUT = 20000           # 单个工具结果回传上限，网页单条消息有长度限制
SKIP_DIRS = {".git", "__pycache__", "node_modules", "chrome_profile", ".venv", "venv", "data"}


# ---------------- 路径 ----------------

def _resolve(p: str) -> Path:
    cand = Path(p)
    if not cand.is_absolute():
        cand = WORKSPACE / cand
    cand = cand.resolve()
    if cand != WORKSPACE and WORKSPACE not in cand.parents:
        raise ValueError(f"路径越界：{p}（只允许 {WORKSPACE} 之内）")
    return cand


def _clip(text: str, limit: int = MAX_OUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[输出过长，已截断，共 {len(text)} 字符]"


# ---------------- 工具实现 ----------------

def run_powershell(command: str, timeout_s: int = 120, **_ignored) -> str:
    """跑一条 PowerShell。UTF-8 输出，超时强杀。"""
    ps = (
        "$ErrorActionPreference='Continue';"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$OutputEncoding=[Text.Encoding]::UTF8;"
        + command
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            cwd=str(WORKSPACE), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {timeout_s}s 被终止"
    except Exception as e:  # noqa: BLE001
        return f"[执行失败] {e!r}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    parts = [f"exit={r.returncode}"]
    if out:
        parts.append(out)
    if err:
        parts.append("[stderr]\n" + err)
    return _clip("\n".join(parts))


def read_file(path: str, offset: int = 1, limit: int = 400, **_ignored) -> str:
    p = _resolve(path)
    if not p.is_file():
        return f"[不存在] {p}"
    raw = p.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return f"[无法解码] {p}（二进制或未知编码，{len(raw)} 字节）"
    lines = text.splitlines()
    start = max(1, int(offset))
    chunk = lines[start - 1:start - 1 + max(1, int(limit))]
    body = "\n".join(f"{i:>5}| {line}" for i, line in enumerate(chunk, start))
    return _clip(f"{p}（共 {len(lines)} 行，显示 {start}-{start + len(chunk) - 1}）\n{body}")


def write_file(path: str, content: str, **_ignored) -> str:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {p}（{len(content)} 字符，{content.count(chr(10)) + 1} 行）"


def list_dir(path: str = ".", **_ignored) -> str:
    p = _resolve(path)
    if not p.is_dir():
        return f"[不是目录] {p}"
    rows = []
    for child in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if child.name in SKIP_DIRS:
            continue
        if child.is_dir():
            rows.append(f"  <dir>  {child.name}/")
        else:
            rows.append(f"{child.stat().st_size:>8}  {child.name}")
    return _clip(f"{p}（{len(rows)} 项）\n" + "\n".join(rows))


def search_text(pattern: str, path: str = ".", include: str = "*.py", max_hits: int = 60, **_ignored) -> str:
    """正则搜索文件内容，跳过 .git 等目录。"""
    root = _resolve(path)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"[正则错误] {e}"
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if include and not Path(fn).match(include):
                continue
            fp = Path(dirpath) / fn
            try:
                if fp.stat().st_size > 2_000_000:
                    continue
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for ln, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{fp.relative_to(WORKSPACE)}:{ln}: {line.strip()[:160]}")
                    if len(hits) >= max_hits:
                        return _clip(f"命中 {len(hits)} 条（已截断）\n" + "\n".join(hits))
    if not hits:
        return f"没有匹配 {pattern!r}（{root}，include={include}）"
    return _clip(f"命中 {len(hits)} 条\n" + "\n".join(hits))


DISPATCH = {
    "run_powershell": run_powershell,
    "read_file": read_file,
    "write_file": write_file,
    "list_dir": list_dir,
    "search_text": search_text,
}


# ---------------- OpenAI function schema ----------------

TOOLS = [
    {"type": "function", "function": {
        "name": "run_powershell",
        "description": "在本机用 Windows PowerShell 5.1 执行命令，返回退出码与输出。运行程序、装依赖、跑测试、git 操作都走它。",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "PowerShell 命令，可多行"},
            "timeout_s": {"type": "integer", "description": "超时秒数，默认 120"},
        }, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读文本文件，返回带行号的内容。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区或绝对路径"},
            "offset": {"type": "integer", "description": "起始行，默认 1"},
            "limit": {"type": "integer", "description": "行数，默认 400"},
        }, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "新建或整体覆盖一个文本文件（自动建目录）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "完整文件内容"},
        }, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "列目录内容（跳过 .git / node_modules / 浏览器 profile）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "默认工作区根"},
        }}}},
    {"type": "function", "function": {
        "name": "search_text",
        "description": "按正则搜索文件内容，返回 文件:行号: 内容。",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Python 正则"},
            "path": {"type": "string", "description": "搜索起点，默认工作区根"},
            "include": {"type": "string", "description": "文件名通配，默认 *.py"},
        }, "required": ["pattern"]}}},
]


def execute(name: str, args: dict) -> str:
    fn = DISPATCH.get(name)
    if fn is None:
        return f"[未知工具] {name}；可用：{', '.join(DISPATCH)}"
    if not isinstance(args, dict):
        return f"[参数错误] {name} 需要 JSON 对象，收到 {type(args).__name__}"
    try:
        return fn(**args)
    except TypeError as e:
        return f"[参数错误] {name}: {e}"
    except Exception as e:  # noqa: BLE001
        return f"[工具异常] {name}: {e!r}"


if __name__ == "__main__":
    print(json.dumps({"workspace": str(WORKSPACE), "tools": [t["function"]["name"] for t in TOOLS]},
                     ensure_ascii=False))
