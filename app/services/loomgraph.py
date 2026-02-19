"""LoomGraph service — thin adapter over MatrixoneGraph.

All logic (indexing, querying, impact analysis, health scoring) lives in
matrixone_graph/. This module manages per-repo instances and exposes the
async API consumed by Manon routers and coach pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from matrixone_graph import MatrixoneGraph

log = logging.getLogger("manon.loomgraph")

# ── Module-level config ──

_EMBEDDING_URL: str = "http://117.131.45.179:3002"
_instances: dict[str, MatrixoneGraph] = {}


def configure(*, embedding_url: str = "", **_kwargs) -> None:
    global _EMBEDDING_URL
    if embedding_url:
        _EMBEDDING_URL = embedding_url


def _get_graph(repo_path: str) -> MatrixoneGraph:
    key = str(Path(repo_path).resolve())
    if key not in _instances:
        _instances[key] = MatrixoneGraph(key, embedding_url=_EMBEDDING_URL)
    return _instances[key]


async def shutdown() -> None:
    for mg in _instances.values():
        try:
            await mg.close()
        except Exception:
            pass
    _instances.clear()


# ── Queries ──

async def search(query: str, *, repo_path: str, mode: str = "hybrid",
                 top_k: int = 10, depth: int = 1) -> dict:
    mg = _get_graph(repo_path)
    result = await mg.query(query, top_k=top_k, depth=depth)
    return {
        "success": True,
        "data": {
            "query": query, "mode": mode,
            "response": result.context,
            "entities": result.entities,
            "relations": result.relations,
            "chunks": result.chunks,
        },
    }


async def graph(symbol: str, *, repo_path: str, depth: int = 2) -> dict:
    mg = _get_graph(repo_path)
    result = await mg.query(symbol, top_k=5, depth=depth)
    return {
        "success": True,
        "data": {
            "symbol": symbol, "depth": depth,
            "response": result.context,
            "entities": result.entities,
            "relations": result.relations,
        },
    }


async def impact(*, file: str | None = None, staged: bool = False,
                 repo_path: str) -> dict:
    """Impact analysis — delegates to MatrixoneGraph graph traversal."""
    mg = _get_graph(repo_path)
    if staged:
        data = mg.impact_staged()
    elif file:
        # For single-file impact, analyze the latest commit
        data = mg.impact_commit()
    else:
        data = mg.impact_commit()
    return {"success": True, "data": data}


async def status(repo_path: str | None = None) -> dict:
    if not repo_path:
        return {"success": False, "data": {"status": "no_repo_path"}}
    mg = _get_graph(repo_path)
    s = mg.status()
    indexed = s.get("indexed", False)
    return {
        "success": indexed,
        "data": {"status": "indexed" if indexed else "not_indexed", **s},
    }


# ── Indexing ──

async def index_repo(
    repo_path: str,
    *,
    incremental: bool = True,
    on_progress: callable | None = None,
) -> dict:
    mg = _get_graph(repo_path)
    result = await mg.index(incremental=incremental, on_progress=on_progress)
    health = await mg.health()
    return {
        "files": result.files_scanned,
        "entities": result.entities_added,
        "relations": result.relations_added,
        "chunks": result.chunks_added,
        "skipped": result.files_skipped,
        "errors": [],
        "entityTypes": {},
        "health": health,
    }


async def update_index(
    repo_path: str,
    *,
    changed_files: list[str] | None = None,
) -> dict:
    return await index_repo(repo_path, incremental=True)
