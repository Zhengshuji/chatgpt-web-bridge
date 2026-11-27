"""中间件 —— 网页版 ChatGPT(经 bridge) 与本地工具、git 之间的那一层。

接口刻意和 DeepSeek Harness 的 agent loop 保持一致：
    POST /v1/chat/completions  {messages, tools}  ->  choices[0].message
    message.tool_calls[]        ->  本地执行        ->  role="tool", tool_call_id=...
    工具结果回填后再次请求，直到某轮不带 tool_calls 为止。

每轮（每次工具执行完）把工作区 commit + push 一次，让网页那头"靠 GitHub 了解项目"
始终看到最新代码；本地是唯一权威，远端只做展示，push 允许 --force。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import TOOLS, WORKSPACE, execute  # noqa: E402

BRIDGE = os.environ.get("AGENT_BRIDGE", "http://127.0.0.1:8355").rstrip("/")
KEY_FILE = Path(os.environ.get("AGENT_KEY_FILE") or (WORKSPACE / "bridge" / "data" / "key.txt"))
MODEL = os.environ.get("AGENT_MODEL", "gpt-web")
BRANCH = os.environ.get("AGENT_BRANCH", "main")
# 远端仓库: 只是给网页那头看的; 不设就只在本地提交
REMOTE = os.environ.get("AGENT_REMOTE", "").strip()
# 没有远端又想自动建一个(需要 gh 已登录): 填仓库名, 如 "chatgpt-web-bridge"
AUTO_REPO = os.environ.get("AGENT_AUTO_REPO", "").strip()
# 打开网页上的"思考"开关: 1 开, 0 关, 不设则听桥那边的默认
THINKING = os.environ.get("AGENT_THINKING", "").strip()
LOG_DIR = WORKSPACE / "logs"

_LOG_FH = None


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _LOG_FH is not None:
        try:
            _LOG_FH.write(line + "\n")
            _LOG_FH.flush()
        except OSError:
            pass

SYSTEM = (
    "本地执行器的工作目录是 {root}，它的 shell 是 Windows PowerShell 5.1（不是 bash，路径用反斜杠）。\n"
    "你每轮给出的命令会在那个目录下真实执行，结果作为下一条消息贴回来。\n"
    "改完代码要自己跑一遍验证；遇到报错先读错误再动手。\n"
    "需要了解项目就列目录、读文件、搜关键字，不要凭印象猜。"
)


class Agent:
    def __init__(self, model: str = MODEL, max_rounds: int = 25, push: bool = True,
                 push_every_round: bool = True, verbose: bool = True, new_chat: bool = False):
        self.model = model
        self.max_rounds = max_rounds
        self.push = push
        self.push_every_round = push_every_round
        self.verbose = verbose
        self.key = KEY_FILE.read_text(encoding="utf-8").strip()
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM.format(root=WORKSPACE)}
        ]
        self.ensure_repo()
        self._ensure_git_identity()
        if new_chat:
            self.start_new_chat()

    # ---------------- git ----------------
    def _git(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(WORKSPACE), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)

    def _is_repo(self) -> bool:
        r = self._git("rev-parse", "--is-inside-work-tree")
        return r.returncode == 0 and r.stdout.strip() == "true"

    def ensure_repo(self) -> None:
        """项目本来没 git 就用它: 就地 init, 有 AGENT_REMOTE 就挂远端, 有 AGENT_AUTO_REPO 就建仓库。"""
        if not self._is_repo():
            log("本目录还不是 git 仓库，先 git init")
            self._git("init")
            self._git("branch", "-M", BRANCH)
        origin = self._git("remote", "get-url", "origin")
        if origin.returncode == 0:
            return
        if REMOTE:
            self._git("remote", "add", "origin", REMOTE)
            log(f"已挂远端 origin = {REMOTE}")
        elif AUTO_REPO:
            log(f"没有远端，尝试 gh repo create {AUTO_REPO}")
            gh = subprocess.run(["gh", "repo", "create", AUTO_REPO, "--public", "--source", ".",
                                 "--remote", "origin", "--push"],
                                cwd=str(WORKSPACE), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180)
            if gh.returncode != 0:
                log(f"gh 建仓库失败（只在本地提交）: {gh.stderr[-160:]}")
            else:
                log(f"远端已建好: {gh.stdout.strip()[:120]}")

    def _ensure_git_identity(self) -> None:
        for k, v in (("user.name", "local-agent"), ("user.email", "local-agent@localhost")):
            if not self._git("config", "--get", k).stdout.strip():
                self._git("config", k, v)

    def git_sync(self, reason: str) -> str:
        """commit + push；没有改动就跳过。远端只是给网页看的，允许强推。"""
        if not self.push:
            return "push 已关闭"
        self._git("add", "-A")
        staged = self._git("status", "--porcelain").stdout.strip()
        if not staged:
            return "无改动"
        n = len(staged.splitlines())
        commit = self._git("commit", "--no-verify", "-m", f"agent: {reason}\n\n自动提交 {n} 个文件")
        if commit.returncode != 0:
            return f"commit 失败: {commit.stdout[-200:]}{commit.stderr[-200:]}"
        push = self._git("push", "--force", "origin", f"HEAD:{BRANCH}", timeout=180)
        if push.returncode != 0:
            return f"已提交 {n} 个文件；push 失败(仅本地): {push.stderr[-160:]}"
        return f"已提交并推送 {n} 个文件"

    # ---------------- HTTP ----------------
    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        if THINKING in ("0", "1"):
            h["X-Thinking"] = "1" if THINKING == "1" else "0"
        return h

    def _chat(self, timeout: int = 600) -> dict:
        body = {"model": self.model, "messages": self.messages, "tools": TOOLS, "stream": False}
        r = requests.post(f"{BRIDGE}/v1/chat/completions", json=body, timeout=timeout,
                          headers=self._headers())
        if r.status_code != 200:
            raise RuntimeError(f"bridge {r.status_code}: {r.text[:400]}")
        return r.json()

    def start_new_chat(self) -> str:
        """让网页那头开一个新对话（不清理本地 messages 指纹由桥自己判断）。"""
        try:
            r = requests.post(f"{BRIDGE}/new_chat", json={"model": self.model},
                              headers=self._headers(), timeout=120)
            info = r.json() if r.ok else {}
            log(f"新对话: {info.get('url', r.status_code)}")
            return info.get("url", "")
        except Exception as e:  # noqa: BLE001
            log(f"开新对话失败: {e!r}")
            return ""

    # ---------------- 主循环 ----------------
    def ask(self, task: str) -> str:
        self.messages.append({"role": "user", "content": task})
        log(f"任务：{task}")
        final = ""
        for rnd in range(1, self.max_rounds + 1):
            t0 = time.time()
            data = self._chat()
            msg = data["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""
            log(f"第 {rnd} 轮：{time.time() - t0:.1f}s，tool_calls={len(calls)}")

            if content.strip() and not calls and self.verbose:
                log(f"模型说明：{content.strip()[:160]}")
            self.messages.append({"role": "assistant", "content": content or None,
                                  **({"tool_calls": calls} if calls else {})})

            if not calls:
                final = content
                log(f"本轮结束：{self.git_sync(f'round {rnd} final')}")
                break

            for tc in calls:
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                log(f"  -> {name}({json.dumps(args, ensure_ascii=False)[:120]})")
                out = execute(name, args)
                head = out.splitlines()[0] if out else ""
                log(f"  <- {len(out)} 字符：{head[:160]}")
                self.messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                      "name": name, "content": out})

            if self.push_every_round:
                log(f"  同步：{self.git_sync(f'round {rnd} tools')}")
        else:
            log(f"达到最大轮数 {self.max_rounds}，强制收尾")
            self.git_sync("max rounds")
        self.dump_transcript()
        return final

    def dump_transcript(self) -> str:
        """把这次会话的完整 messages 落到 logs/，出问题能复现。"""
        try:
            LOG_DIR.mkdir(exist_ok=True)
            path = LOG_DIR / f"transcript-{time.strftime('%Y%m%d-%H%M%S')}.json"
            path.write_text(json.dumps(self.messages, ensure_ascii=False, indent=1), encoding="utf-8")
            log(f"对话记录: {path}")
            return str(path)
        except OSError as e:
            log(f"写对话记录失败: {e!r}")
            return ""


def _open_log() -> None:
    global _LOG_FH
    LOG_DIR.mkdir(exist_ok=True)
    _LOG_FH = open(LOG_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}.log", "a", encoding="utf-8")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="本地 agent：网页版 ChatGPT 当大脑，PowerShell 当手脚")
    ap.add_argument("task", nargs="?", help="任务描述；不给则进交互模式")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--max-rounds", type=int, default=25)
    ap.add_argument("--no-push", action="store_true", help="不提交不推送")
    ap.add_argument("--push-end-only", action="store_true", help="只在整轮结束时推送")
    ap.add_argument("--new-chat", action="store_true", help="开会话前先让网页那边新建一个对话")
    ap.add_argument("--thinking", choices=["1", "0"], help="覆盖网页上的思考开关")
    args = ap.parse_args()

    global THINKING
    if args.thinking is not None:
        THINKING = args.thinking

    _open_log()
    agent = Agent(model=args.model, max_rounds=args.max_rounds, push=not args.no_push,
                  push_every_round=not args.push_end_only, new_chat=args.new_chat)
    agent.dump_transcript()

    if args.task:
        print(agent.ask(args.task))
        return 0

    print("交互模式，空行退出。")
    while True:
        try:
            task = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not task:
            break
        print(agent.ask(task))
    return 0


if __name__ == "__main__":
    sys.exit(main())
