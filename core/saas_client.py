"""saas/ API async client — shared data layer for web + MCP clients.

All graph operations (search, graph, impact, deep-query) and repo management
go through saas/ REST API.

Usage:
    from core import saas_client
    saas_client.configure("http://localhost:3700", "my-api-key")
    result = await saas_client.search(repo_id, "auth flow")
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("manon.saas_client")

# ── Module-level config (set via configure()) ────────

_saas_url: str = "http://localhost:3700"
_api_key: str = ""
_HTTP_TIMEOUT = 45


def configure(saas_url: str, api_key: str = "") -> None:
    """Set saas/ backend URL and API key. Call once at startup."""
    global _saas_url, _api_key
    _saas_url = saas_url.rstrip("/")
    _api_key = api_key
    log.info("saas_client configured: %s", _saas_url)


# ── Internal helpers ─────────────────────────────────

def _base_url() -> str:
    return _saas_url


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key}", "Content-Type": "application/json"}


async def _get(path: str, *, timeout: int = _HTTP_TIMEOUT, **params) -> Any:
    async with httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=timeout) as c:
        r = await c.get(path, params=params)
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict, *, timeout: int = _HTTP_TIMEOUT) -> Any:
    async with httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=timeout) as c:
        r = await c.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> None:
    async with httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=_HTTP_TIMEOUT) as c:
        r = await c.delete(path)
        r.raise_for_status()


# ── Repos CRUD ───────────────────────────────────────

async def repos_create(
    name: str, *, git_url: str = "", local_path: str = "",
    source_type: str = "local", branch: str = "main",
) -> dict:
    body: dict[str, Any] = {"name": name, "branch": branch, "source_type": source_type}
    if git_url:
        body["git_url"] = git_url
    if local_path:
        body["local_path"] = local_path
    return await _post("/api/v1/repos", body)


async def repos_list() -> list[dict]:
    return await _get("/api/v1/repos")


async def repos_get(repo_id: str) -> dict:
    return await _get(f"/api/v1/repos/{repo_id}")


async def repos_delete(repo_id: str) -> None:
    await _delete(f"/api/v1/repos/{repo_id}")


# ── Query ────────────────────────────────────────────

async def search(repo_id: str, query: str, *, top_k: int = 10, depth: int = 1) -> dict:
    return await _get(f"/api/v1/repos/{repo_id}/search", q=query, top_k=top_k, depth=depth)


async def graph(repo_id: str, symbol: str, *, depth: int = 2, direction: str = "both") -> dict:
    return await _get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth, direction=direction)


async def impact(repo_id: str, *, commit: str = "HEAD", max_depth: int = 3) -> dict:
    return await _get(f"/api/v1/repos/{repo_id}/impact", commit=commit, max_depth=max_depth)


# ── Indexing / Sync ──────────────────────────────────

async def sync_ast(repo_id: str, files: list[dict], deleted_files: list[str], *, full_reindex: bool = False) -> dict:
    return await _post(f"/api/v1/repos/{repo_id}/sync-ast", {
        "files": files, "deleted_files": deleted_files, "full_reindex": full_reindex,
    })


async def push_update(repo_id: str) -> dict:
    return await _post(f"/api/v1/repos/{repo_id}/push-update", {})


async def index_status(repo_id: str) -> dict:
    return await _get(f"/api/v1/repos/{repo_id}/index-status")


async def trigger_index(repo_id: str, *, incremental: bool = True) -> dict:
    return await _post(f"/api/v1/repos/{repo_id}/index", {"incremental": incremental})


# ── Health ───────────────────────────────────────────

async def health() -> dict:
    async with httpx.AsyncClient(base_url=_base_url(), timeout=10) as c:
        r = await c.get("/health")
        r.raise_for_status()
        return r.json()

