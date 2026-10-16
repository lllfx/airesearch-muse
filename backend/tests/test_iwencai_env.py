from __future__ import annotations

import importlib.util
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOADER_PATH = REPO_ROOT / "dsh" / "skills" / "_iwencai_env.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("iwencai_env_test", LOADER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_iwencai_env_recovers_scrubbed_key(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "IWENCAI_BASE_URL=https://example.test\n"
        'IWENCAI_API_KEY="test-key"\n'
        "OPENAI_API_KEY=must-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MUSE_ENV_FILE", str(env_file))
    monkeypatch.delenv("IWENCAI_BASE_URL", raising=False)
    monkeypatch.delenv("IWENCAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    _load_module().load_iwencai_env()

    assert os.environ["IWENCAI_BASE_URL"] == "https://example.test"
    assert os.environ["IWENCAI_API_KEY"] == "test-key"
    assert "OPENAI_API_KEY" not in os.environ
