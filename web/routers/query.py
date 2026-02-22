"""Query + init endpoints — /api/v1/projects/{id}/*.

All graph operations go through saas/ API via saas_client.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_api_key
from shared import saas_client
from shared.ast_sync import find_project_by_repo_id, get_project, set_project

log = logging.getLogger("manon.query")

router = APIRouter(tags=["query"], dependencies=[Depends(require_api_key)])


@router.get("/projects/{project_id}/search")
async def search(project_id: str, q: str = Query(...), mode: str = Query("hybrid")):
    result = await saas_client.search(project_id, q, top_k=10, depth=1)
    return {"result": {"success": True, "data": {
        "query": q, "mode": mode, "response": result.get("context", ""),
        "entities": result.get("entities", []),
        "relations": result.get("relations", []),
        "chunks": result.get("chunks", []),
    }}}


@router.get("/projects/{project_id}/graph")
async def graph(
    project_id: str,
    symbol: str = Query(...),
    direction: str = Query("both"),
    depth: int = Query(2),
):
    result = await saas_client.graph(project_id, symbol, depth=depth)
    return {"result": {"success": True, "data": {
        "symbol": symbol, "depth": depth,
        "response": result.get("context", ""),
        "entities": result.get("entities", []),
        "relations": result.get("relations", []),
    }}}


@router.get("/projects/{project_id}/impact")
async def impact(project_id: str, file: str | None = Query(None), staged: bool = Query(False)):
    result = await saas_client.impact(project_id)
    return {"result": {"success": True, "data": result}}


@router.get("/projects/{project_id}/stats")
async def stats(project_id: str):
    """Get index stats from saas/."""
    default = {"entities": 0, "relations": 0, "files": 0, "chunks": 0}
    try:
        repo = await saas_client.repos_get(project_id)
        raw = repo.get("index_stats", {})
        return {
            "entities": raw.get("total_entities", raw.get("entities_added", 0)),
            "relations": raw.get("total_relations", raw.get("relations_added", 0)),
            "files": raw.get("total_files", raw.get("files_synced", 0)),
            "chunks": raw.get("total_chunks", raw.get("chunks_added", 0)),
        }
    except Exception:
        return default


@router.get("/projects/{project_id}/status")
async def loomgraph_status(project_id: str):
    """Check index status via saas/."""
    try:
        status = await saas_client.index_status(project_id)
        indexed = status.get("status") == "done"
        return {"success": indexed, "data": {"status": "indexed" if indexed else status.get("status", "unknown"), **status}}
    except Exception as exc:
        return {"success": False, "data": {"status": "error", "error": str(exc)}}


@router.post("/projects/{project_id}/init")
async def init_project(project_id: str):
    """Initialize project — check saas/ index status."""
    results: dict = {}
    try:
        status = await saas_client.index_status(project_id)
        results["status_check"] = {"success": status.get("status") == "done", "data": status}
    except Exception as exc:
        results["status_check"] = {"error": str(exc)}
    try:
        repo = await saas_client.repos_get(project_id)
        stats = repo.get("index_stats", {})
        results["stats"] = stats
        results["status"] = "ok" if stats else "not_indexed"
    except Exception:
        results["stats"] = {"entities": 0, "relations": 0, "files": 0, "chunks": 0}
        results["status"] = "not_indexed"
    return results


@router.post("/projects/{project_id}/index")
async def index_project(project_id: str, clear: bool = Query(False)):
    """Trigger indexing — AST sync for local projects, server-side for git."""
    from shared.ast_sync import scan_and_parse, sync_to_server
    from datetime import datetime

    found = find_project_by_repo_id(project_id)
    if found:
        local_path, info = found
        old_hashes = {} if clear else info.get("file_hashes", {})
        file_results, deleted, new_hashes = scan_and_parse(local_path, old_hashes)
        if file_results or deleted:
            await sync_to_server(project_id, file_results, deleted, full_reindex=clear)
        info["file_hashes"] = new_hashes
        info["last_sync"] = datetime.now().isoformat()
        set_project(local_path, info)
        return {"result": {"files_synced": len(file_results), "deleted": len(deleted), "total": len(new_hashes)}}

    result = await saas_client.trigger_index(project_id, incremental=not clear)
    return {"result": result}
