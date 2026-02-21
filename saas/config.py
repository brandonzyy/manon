from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class SaasSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAAS_")

    port: int = 3700
    db_path: str = "./saas.db"
    repos_dir: str = "./saas_repos"
    index_dir: str = "./saas_indexes"
    embedding_url: str = "http://117.131.45.179:3002"

    # LLM (OpenAI-compatible)
    llm_api_url: str = "https://api.matrixone.online/v1/chat/completions"
    llm_model: str = "glm-4.7-fp8"
    llm_api_key: str = ""

    # rate limits (requests / minute)
    rate_free: int = 30
    rate_pro: int = 300
    rate_enterprise: int = 3000

    def rate_for(self, tier: str) -> int:
        return {"free": self.rate_free, "pro": self.rate_pro, "enterprise": self.rate_enterprise}.get(tier, self.rate_free)

    def ensure_dirs(self) -> None:
        Path(self.repos_dir).mkdir(parents=True, exist_ok=True)
        Path(self.index_dir).mkdir(parents=True, exist_ok=True)


settings = SaasSettings()
