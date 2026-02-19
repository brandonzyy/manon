"""Manon Gateway settings — loaded from env vars (MANON_ prefix) or manon.yaml."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve venv paths
_MANON_ROOT = Path(__file__).resolve().parent.parent
_VENV_BIN = _MANON_ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")


class ManonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MANON_")

    port: int = 3600
    db_path: str = "./manon.db"
    repos_dir: str = "./repos"

    # LoomGraph / CodeIndex — local venv binaries
    loomgraph_bin: str = str(_VENV_BIN / "loomgraph")
    codeindex_bin: str = str(_VENV_BIN / "codeindex")
    lightrag_url: str = "http://117.131.45.179:3010"
    embedding_url: str = "http://117.131.45.179:3002"
    loomgraph_workspace: str = "manon_default"

    # API Server (auto-fix hub)
    api_server_ws: str = "ws://localhost:3501/ws/coach"

    # LLM
    llm_api_url: str = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
    llm_model: str = "GLM-5"
    llm_model_fallback: str = "GLM-4.7"
    llm_api_key: str = ""

    # Auth
    api_keys: list[str] = []


_settings: ManonSettings | None = None


def get_settings() -> ManonSettings:
    global _settings
    if _settings is None:
        _settings = ManonSettings()
    return _settings
