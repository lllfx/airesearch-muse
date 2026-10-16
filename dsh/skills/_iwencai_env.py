"""Load Iwencai settings from the project .env for sandboxed skill calls.

DSH deliberately removes inherited variables whose names look like secrets
before executing model-requested shell commands.  The Iwencai scripts still
prefer their normal process environment, but can recover the project-local
settings from .env when the shell scrubbed IWENCAI_API_KEY.
"""

from __future__ import annotations

import os
from pathlib import Path


_ALLOWED_NAMES = {
    "IWENCAI_API_KEY",
    "IWENCAI_BASE_URL",
    "IWENCAI_ENDPOINT",
    "IWENCAI_TIMEOUT",
}


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_iwencai_env() -> None:
    """Fill missing Iwencai variables from the repository's ignored .env."""
    configured = os.environ.get("MUSE_ENV_FILE")
    env_path = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in _ALLOWED_NAMES or name in os.environ:
            continue
        os.environ[name] = _unquote(value)
