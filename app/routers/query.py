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
    result = await loomgraph.search(q, mode=mode, workspace=proj["workspace"])
    return {"result": result}


@router.get("/projects/{project_id}/graph")
async def graph(
    project_id: str,
    symbol: str = Query(...),
    direction: str = Query("both"),
    depth: int = Query(2),
):
    proj = await _get_project(project_id)
    result = await loomgraph.graph(symbol, direction=direction, depth=depth, workspace=proj["workspace"])
    return {"result": result}


@router.get("/projects/{project_id}/impact")
async def impact(project_id: str, file: str | None = Query(None), staged: bool = Query(False)):
    proj = await _get_project(project_id)
    result = await loomgraph.impact(file=file, staged=staged, workspace=proj["workspace"])
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
    """Check LoomGraph system status (LightRAG, embedding, codeindex)."""
    return await loomgraph.status()


@router.post("/projects/{project_id}/init")
async def init_project(project_id: str):
    """Initialize LoomGraph for the project — verify connectivity + test query."""
    proj = await _get_project(project_id)
    results = {}
    # 1. Check system status
    try:
        results["status_check"] = await loomgraph.status()
    except Exception as exc:
        results["status_check"] = {"error": str(exc)}
    # 2. Test query
    try:
        result = await loomgraph.search("project structure overview", mode="local", workspace=proj["workspace"])
        results["stats"] = loomgraph.parse_response_stats(result)
        results["status"] = "ok"
    except Exception as exc:
        results["status"] = "error"
        results["error"] = str(exc)
    return results


@router.post("/projects/{project_id}/index")
async def index_project(project_id: str, clear: bool = Query(False)):
    """Run full LoomGraph indexing for the project."""
    proj = await _get_project(project_id)
    local_path = proj["local_path"]
    workspace = proj["workspace"]
    if not local_path:
        raise HTTPException(400, "Project has no local_path")
    results = []
    # Index each standard directory
    dirs = ["agent", "electron", "renderer", "skills"]
    for i, d in enumerate(dirs):
        try:
            r = await loomgraph.index_dir(d, clear=(clear and i == 0), workspace=workspace, cwd=local_path)
            results.append({"dir": d, "status": "ok", "result": r})
        except Exception as exc:
            results.append({"dir": d, "status": "error", "error": str(exc)})
    return {"results": results}


async def _get_project(project_id: str) -> dict:
    async with db_pool() as db:
        row = await db.execute_fetchone(
            "SELECT id, local_path, workspace FROM projects WHERE id=?", (project_id,)
        )
    if not row:
        raise HTTPException(404, "Project not found")
    return {"id": row[0], "local_path": row[1], "workspace": row[2]}
