from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _runtime_root() -> Path:
    """Return the default local runtime root for SaaS state."""
    explicit = os.environ.get("SAAS_RUNTIME_ROOT") or os.environ.get("MANON_RUNTIME_DIR")
    if explicit:
        return Path(explicit)
    return Path(".manon_runtime") / "saas"


RUNTIME_ROOT = _runtime_root()


class SaasSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAAS_")

    port: int = 3700
    db_path: str = str(RUNTIME_ROOT / "saas.db")
    repos_dir: str = str(RUNTIME_ROOT / "repos")
    index_dir: str = str(RUNTIME_ROOT / "indexes")
    data_dir: str = str(RUNTIME_ROOT / "data")  # JSONL training logs
    embedding_url: str = "http://127.0.0.1:3002"

    # LLM (OpenAI-compatible)
    # Default: local Ollama. For production, set SAAS_LLM_API_URL env var
    # Examples: http://localhost:11434/v1/chat/completions (Ollama)
    #           https://api.openai.com/v1/chat/completions (OpenAI)
    llm_api_url: str = Field(
        default="http://localhost:11434/v1/chat/completions",
        validation_alias=AliasChoices("SAAS_LLM_API_URL", "MANON_LLM_API_URL"),
    )
    llm_model: str = Field(
        default="qwen2.5-coder:7b",
        validation_alias=AliasChoices("SAAS_LLM_MODEL", "MANON_LLM_MODEL"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("SAAS_LLM_API_KEY", "MANON_LLM_API_KEY"),
    )

    # admin
    admin_secret: str = ""  # set SAAS_ADMIN_SECRET env var

    # free trial duration (days from registration)
    free_trial_days: int = 30

    # quotas per tier
    quota_repos_free: int = 1
    quota_repos_pro: int = 5
    quota_repos_enterprise: int = 9999

    def quota_repos(self, tier: str) -> int:
        return {"free": self.quota_repos_free, "pro": self.quota_repos_pro, "enterprise": self.quota_repos_enterprise}.get(tier, self.quota_repos_free)

    def ensure_dirs(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.repos_dir).mkdir(parents=True, exist_ok=True)
        Path(self.index_dir).mkdir(parents=True, exist_ok=True)
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)


settings = SaasSettings()
