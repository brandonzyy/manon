"""Pydantic request / response models."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ── Repos ──────────────────────────────────────────────
class RepoCreate(BaseModel):
    name: str
    git_url: str = ""
    branch: str = "main"
    local_path: str | None = None


class RepoOut(BaseModel):
    id: str
    name: str
    git_url: str
    branch: str
    local_path: str | None
    index_status: str
    index_stats: dict | None = None
    created_at: str
    updated_at: str


# ── Indexing ───────────────────────────────────────────
class IndexTrigger(BaseModel):
    incremental: bool = True


class IndexStatus(BaseModel):
    repo_id: str
    status: str
    stats: dict | None = None


# ── Query ──────────────────────────────────────────────
class SearchResult(BaseModel):
    entities: list[dict] = []
    relations: list[dict] = []
    chunks: list[dict] = []
    context: str = ""


class ImpactResult(BaseModel):
    commit: str = ""
    changed_symbols: list = []
    direct_callers: list = []
    indirect_callers: list = []
    affected_modules: list = []
    affected_tests: list = []
    risk: dict = {}


# ── Usage ──────────────────────────────────────────────
class UsageSummary(BaseModel):
    tenant_id: str
    period_days: int
    total_calls: int
    total_tokens: int
    by_endpoint: dict[str, int] = {}


# ── Admin / Seed ───────────────────────────────────────
class TenantCreate(BaseModel):
    name: str
    tier: str = "free"


class TenantOut(BaseModel):
    id: str
    name: str
    tier: str
    api_key: str = ""
