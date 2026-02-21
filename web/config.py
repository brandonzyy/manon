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
    model_config = SettingsConfigDict(env_prefix="MANON_", env_file=".env", env_file_encoding="utf-8")

    port: int = 3600
    db_path: str = "./manon.db"
    repos_dir: str = "./repos"
    index_dir: str = "./indexes"

    # CodeIndex (local AST parsing)
    codeindex_bin: str = str(_VENV_BIN / "codeindex")
    embedding_url: str = "http://117.131.45.179:3002"

    # API Server (auto-fix hub)
    api_server_ws: str = "ws://localhost:3501/ws/coach"

    # LLM
    llm_api_url: str = "https://api.matrixone.online/v1/chat/completions"
    llm_model: str = "glm-4.7-fp8"
    llm_model_fallback: str = "GLM-5"
    llm_api_key: str = "sk-f05sj8cb25syBlnH3pUFN9TuczxgwtEtIEwQ5PEtD22sxeH1"

    # SaaS backend
    saas_url: str = "http://localhost:3700"
    saas_api_key: str = ""

    # Auth
    api_keys: list[str] = []


_settings: ManonSettings | None = None


def get_settings() -> ManonSettings:
    global _settings
    if _settings is None:
        _settings = ManonSettings()
    return _settings
