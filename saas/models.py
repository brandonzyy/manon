"""Pydantic request / response models."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ── Repos ──────────────────────────────────────────────
class RepoCreate(BaseModel):
    name: str
    git_url: str = ""
    branch: str = "main"
    local_path: str | None = None
    source_type: str = ""  # "" = git (default), "local" = client-side AST sync


class RepoOut(BaseModel):
    id: str
    name: str
    git_url: str
    branch: str
    local_path: str | None
    source_type: str = ""
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


class FileSyncData(BaseModel):
    rel_path: str           # relative path within repo
    hash: str               # sha256 of file content
    parse_result: dict      # ParseResult.to_dict()
    chunks: list[dict] = [] # pre-chunked by client (chunk_file_from_dict)
    source: str = ""        # deprecated: kept for transition, use chunks


class SyncAstRequest(BaseModel):
    files: list[FileSyncData] = []
    deleted_files: list[str] = []
    full_reindex: bool = False


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


class RegisterRequest(BaseModel):
    name: str = "anonymous"


# ── Deep Query ────────────────────────────────────────
class DeepQueryRequest(BaseModel):
    question: str
    max_rounds: int = Field(3, ge=1, le=5)


class ImpactLocalRequest(BaseModel):
    """Client sends local git-diff results; server resolves callers in bulk."""
    commit_info: str = ""
    changed_files: list[str] = []
    changed_symbols: list[dict] = []  # [{name, file, added, deleted}]
    max_depth: int = Field(2, ge=1, le=5)


# ── Dynamic Merge ────────────────────────────────────
class MergeDynamicRequest(BaseModel):
    edges: dict[str, int] = {}  # {"caller->callee": count}
    raw_edges: list[dict[str, str]] = []  # [{"from": path, "to": path}] — resolved server-side
    project_root: str = ""  # required when raw_edges is used
