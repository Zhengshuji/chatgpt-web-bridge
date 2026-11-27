"""桥的配置：所有环境变量只在这里读一次，别的地方只读 Config 字段。

好处很直接——想知道"当前跑的是什么配置"，打印一个对象就够；单测也能直接构造 Config，
不用去改进程环境。
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # 链路
    cdp: str = field(default_factory=lambda: os.environ.get("GPTWEB_CDP", "http://127.0.0.1:9225").rstrip("/"))
    port: int = field(default_factory=lambda: int(os.environ.get("GPTWEB_PORT", "8355")))
    # 目录
    home: Path = field(default_factory=lambda: BRIDGE_DIR)
    data_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("GPTWEB_HOME") or (BRIDGE_DIR / "data")))
    # 提示词
    repo: str = field(default_factory=lambda: os.environ.get("GPTWEB_REPO", "").strip())
    policy: str = field(default_factory=lambda: os.environ.get("GPTWEB_POLICY", "").strip())
    prompt_cap: int = 24000
    tool_budget: int = field(default_factory=lambda: int(os.environ.get("GPTWEB_TOOL_BUDGET", "6000")))
    # 行为
    thinking: bool = field(default_factory=lambda: _flag("GPTWEB_THINKING"))
    fastpath: bool = field(default_factory=lambda: _flag("GPTWEB_FASTPATH", "1"))
    dump: bool = field(default_factory=lambda: _flag("GPTWEB_DUMP"))
    session_rounds: int = field(default_factory=lambda: int(os.environ.get("GPTWEB_SESSION_ROUNDS", "20")))
    idle_timeout: float = field(default_factory=lambda: float(os.environ.get("GPTWEB_IDLE_TIMEOUT", "60")))
    total_timeout: float = field(default_factory=lambda: float(os.environ.get("GPTWEB_TOTAL_TIMEOUT", "300")))
    # 运维
    log_max_bytes: int = field(default_factory=lambda: int(os.environ.get("GPTWEB_LOG_MAX", str(5 * 1024 * 1024))))

    @property
    def key_file(self) -> Path:
        return self.data_dir / "key.txt"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "bridge.log"

    @property
    def sessions_file(self) -> Path:
        return self.data_dir / "sessions.json"

    def describe(self) -> str:
        """启动日志里回显一行，排查"到底跑的是哪套配置"。"""
        return (f"cdp={self.cdp} port={self.port} data={self.data_dir} "
                f"thinking={int(self.thinking)} fastpath={int(self.fastpath)} "
                f"dump={int(self.dump)} cap={self.prompt_cap} tool_budget={self.tool_budget} "
                f"idle={self.idle_timeout:.0f}s rounds={self.session_rounds}")


CFG = Config()
