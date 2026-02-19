"""LoomGraph query + init endpoints — /api/v1/projects/{id}/*."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_api_key
from ..db import db_pool
from ..services import loomgraph

router = APIRouter(tags=["query"], dependencies=[Depends(require_api_key)])


@router.get("/projects/{project_id}/search")
async def search(project_id: str, q: str = Query(...), mode: str = Query("local")):
    proj = await _get_project(project_id)
    result = await loomgraph.search(q, mode=mode, repo_path=proj["local_path"])
    return {"result": result}


@router.get("/projects/{project_id}/graph")
async def graph(
    project_id: str,
    symbol: str = Query(...),
    direction: str = Query("both"),
    depth: int = Query(2),
):
    proj = await _get_project(project_id)
    result = await loomgraph.graph(symbol, depth=depth, repo_path=proj["local_path"])
    return {"result": result}


@router.get("/projects/{project_id}/impact")
async def impact(project_id: str, file: str | None = Query(None), staged: bool = Query(False)):
    proj = await _get_project(project_id)
    result = await loomgraph.impact(file=file, staged=staged, repo_path=proj["local_path"])
    return {"result": result}
@router.get("/projects/{project_id}/stats")
async def stats(project_id: str):
    """Get LoomGraph stats for the project — from DB index_stats."""
    import json as _json
    async with db_pool() as db:
        row = await db.execute_fetchone("SELECT index_stats FROM projects WHERE id=?", (project_id,))
    if row and row["index_stats"]:
        return _json.loads(row["index_stats"])
    return {"entities": 0, "relations": 0, "files": 0, "chunks": 0}


@router.get("/projects/{project_id}/status")
async def loomgraph_status(project_id: str):
    """Check MatrixoneGraph index status for the project."""
    proj = await _get_project(project_id)
    return await loomgraph.status(repo_path=proj["local_path"])


@router.post("/projects/{project_id}/init")
async def init_project(project_id: str):
    """Initialize MatrixoneGraph for the project — verify index + return DB stats."""
    proj = await _get_project(project_id)
    results = {}
    # 1. Check index status
    try:
        results["status_check"] = await loomgraph.status(repo_path=proj["local_path"])
    except Exception as exc:
        results["status_check"] = {"error": str(exc)}
    # 2. Return stats from DB
    import json as _json
    from ..db import db_pool as _db_pool
    async with _db_pool() as db:
        row = await db.execute_fetchone("SELECT index_stats FROM projects WHERE id=?", (project_id,))
    if row and row["index_stats"]:
        stats = _json.loads(row["index_stats"])
        stats.pop("status", None)
        results["stats"] = stats
        results["status"] = "ok"
    else:
        results["stats"] = {"entities": 0, "relations": 0, "files": 0, "chunks": 0}
        results["status"] = "not_indexed"
    return results


@router.post("/projects/{project_id}/index")
async def index_project(project_id: str, clear: bool = Query(False)):
    """Run MatrixoneGraph indexing for the project."""
    proj = await _get_project(project_id)
    local_path = proj["local_path"]
    if not local_path:
        raise HTTPException(400, "Project has no local_path")
    result = await loomgraph.index_repo(local_path, incremental=not clear)
    return {"result": result}


async def _get_project(project_id: str) -> dict:
    async with db_pool() as db:
        row = await db.execute_fetchone(
            "SELECT id, local_path, workspace FROM projects WHERE id=?", (project_id,)
        )
    if not row:
        raise HTTPException(404, "Project not found")
    return {"id": row[0], "local_path": row[1], "workspace": row[2]}
