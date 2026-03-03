from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class SaasSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAAS_")

    port: int = 3700
    db_path: str = "./saas.db"
    repos_dir: str = "./saas_repos"
    index_dir: str = "./saas_indexes"
    embedding_url: str = "http://127.0.0.1:3002"

    # LLM (OpenAI-compatible)
    llm_api_url: str = "https://api.matrixone.online/v1/chat/completions"
    llm_model: str = "glm-4.7-fp8"
    llm_api_key: str = ""

    # admin
    admin_secret: str = ""  # set SAAS_ADMIN_SECRET env var

    # rate limits (requests / minute)
    rate_free: int = 30
    rate_pro: int = 300
    rate_enterprise: int = 3000

    # quotas per tier
    quota_repos_free: int = 2
    quota_repos_pro: int = 20
    quota_repos_enterprise: int = 9999
    quota_deep_query_free: int = 10       # per day
    quota_deep_query_pro: int = 9999
    quota_deep_query_enterprise: int = 9999

    def rate_for(self, tier: str) -> int:
        return {"free": self.rate_free, "pro": self.rate_pro, "enterprise": self.rate_enterprise}.get(tier, self.rate_free)

    def quota_repos(self, tier: str) -> int:
        return {"free": self.quota_repos_free, "pro": self.quota_repos_pro, "enterprise": self.quota_repos_enterprise}.get(tier, self.quota_repos_free)

    def quota_deep_query(self, tier: str) -> int:
        return {"free": self.quota_deep_query_free, "pro": self.quota_deep_query_pro, "enterprise": self.quota_deep_query_enterprise}.get(tier, self.quota_deep_query_free)

    def ensure_dirs(self) -> None:
        Path(self.repos_dir).mkdir(parents=True, exist_ok=True)
        Path(self.index_dir).mkdir(parents=True, exist_ok=True)


settings = SaasSettings()
