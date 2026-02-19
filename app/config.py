"""Manon Gateway settings — loaded from env vars (MANON_ prefix) or manon.yaml."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ManonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MANON_")

    port: int = 3600
    db_path: str = "./manon.db"
    repos_dir: str = "./repos"

    # LoomGraph
    loomgraph_bin: str = "loomgraph"

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
