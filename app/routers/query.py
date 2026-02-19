"""Query + init endpoints — /api/v1/projects/{id}/*."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_api_key
from ..db import db_pool
from matrixone_graph import MatrixoneGraph

router = APIRouter(tags=["query"], dependencies=[Depends(require_api_key)])


@router.get("/projects/{project_id}/search")
async def search(project_id: str, q: str = Query(...), mode: str = Query("local")):
    proj = await _get_project(project_id)
    mg = MatrixoneGraph.get(proj["local_path"])
    result = await mg.query(q, top_k=10, depth=1)
    return {"result": {"success": True, "data": {
        "query": q, "mode": mode, "response": result.context,
        "entities": result.entities, "relations": result.relations, "chunks": result.chunks,
    }}}


@router.get("/projects/{project_id}/graph")
async def graph(
    project_id: str,
    symbol: str = Query(...),
    direction: str = Query("both"),
    depth: int = Query(2),
):
    proj = await _get_project(project_id)
    mg = MatrixoneGraph.get(proj["local_path"])
    result = await mg.query(symbol, top_k=5, depth=depth)
    return {"result": {"success": True, "data": {
        "symbol": symbol, "depth": depth, "response": result.context,
        "entities": result.entities, "relations": result.relations,
    }}}


@router.get("/projects/{project_id}/impact")
async def impact(project_id: str, file: str | None = Query(None), staged: bool = Query(False)):
    proj = await _get_project(project_id)
    mg = MatrixoneGraph.get(proj["local_path"])
    data = mg.impact_staged() if staged else mg.impact_commit()
    return {"result": {"success": True, "data": data}}
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
    mg = MatrixoneGraph.get(proj["local_path"])
    s = mg.status()
    indexed = s.get("indexed", False)
    return {"success": indexed, "data": {"status": "indexed" if indexed else "not_indexed", **s}}


@router.post("/projects/{project_id}/init")
async def init_project(project_id: str):
    """Initialize MatrixoneGraph for the project — verify index + return DB stats."""
    proj = await _get_project(project_id)
    results = {}
    # 1. Check index status
    try:
        mg = MatrixoneGraph.get(proj["local_path"])
        s = mg.status()
        results["status_check"] = {"success": s.get("indexed", False), "data": s}
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
    mg = MatrixoneGraph.get(local_path)
    result = await mg.index_report(incremental=not clear)
    return {"result": result}


async def _get_project(project_id: str) -> dict:
    async with db_pool() as db:
        row = await db.execute_fetchone(
            "SELECT id, local_path, workspace FROM projects WHERE id=?", (project_id,)
        )
    if not row:
        raise HTTPException(404, "Project not found")
    return {"id": row[0], "local_path": row[1], "workspace": row[2]}
