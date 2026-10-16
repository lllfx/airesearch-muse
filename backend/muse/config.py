"""运行配置，全部来自环境变量，便于容器化。"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    host: str = os.environ.get("MUSE_HOST", "127.0.0.1")
    port: int = int(os.environ.get("MUSE_PORT", "8787"))
    db_path: Path = Path(os.environ.get("MUSE_DB", REPO_ROOT / "data" / "muse.sqlite3"))
    workspace: Path = Path(os.environ.get("MUSE_WORKSPACE", REPO_ROOT / "workspace"))
    token: str = os.environ.get("MUSE_TOKEN", "dev")

    # 引擎：dsh（真实）或 mock（离线演示 / 测试）
    engine: str = os.environ.get("MUSE_ENGINE", "dsh")
    dsh_bin: list[str] = field(default_factory=lambda: shlex.split(os.environ.get("MUSE_DSH_BIN", "npx --yes @deepseek-ai/dsh")))
    dsh_home: Path = Path(os.environ.get("DSH_HOME", REPO_ROOT / ".dsh-home"))
    dsh_patch: Path = Path(os.environ.get("MUSE_DSH_PATCH", REPO_ROOT / "dsh" / "muse.patch.yml"))
    skill_dir: Path = Path(os.environ.get("MUSE_SKILL_DIR", REPO_ROOT / "dsh" / "skills"))
    provider: str = os.environ.get("MUSE_PROVIDER", "deepseek-official")
    model: str = os.environ.get("MUSE_MODEL", "deepseek-flash")
    max_concurrent_tasks: int = int(os.environ.get("MUSE_MAX_CONCURRENT", "3"))

    @property
    def mcp_url(self) -> str:
        # Starlette mounts the MCP ASGI app below /mcp; the streamable HTTP
        # endpoint itself is "/", so clients must retain the trailing slash.
        return f"http://{self.host}:{self.port}/mcp/"


settings = Settings()
