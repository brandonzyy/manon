"""LoomGraph query endpoints — /api/v1/projects/{id}/query/*."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import require_api_key
from ..db import db_pool
from ..services import loomgraph

router = APIRouter(tags=["query"], dependencies=[Depends(require_api_key)])


async def _get_workspace(project_id: str) -> str:
    async with db_pool() as db:
        row = await db.execute_fetchone("SELECT workspace FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(404, "Project not found")
    return row["workspace"]


@router.get("/projects/{project_id}/search")
async def search(project_id: str, q: str = Query(...), mode: str = Query("local")):
    ws = await _get_workspace(project_id)
    result = await loomgraph.search(q, workspace=ws, mode=mode)
    return {"result": result}


@router.get("/projects/{project_id}/graph")
async def graph(
    project_id: str,
    symbol: str = Query(...),
    direction: str = Query("both"),
    depth: int = Query(2),
):
    ws = await _get_workspace(project_id)
    result = await loomgraph.graph(symbol, workspace=ws, direction=direction, depth=depth)
    return {"result": result}


@router.get("/projects/{project_id}/impact")
async def impact(project_id: str, file: str | None = Query(None), staged: bool = Query(False)):
    ws = await _get_workspace(project_id)
    result = await loomgraph.impact(workspace=ws, file=file, staged=staged)
    return {"result": result}


@router.get("/projects/{project_id}/deps")
async def deps(project_id: str, symbol: str = Query(...)):
    ws = await _get_workspace(project_id)
    result = await loomgraph.deps(symbol, workspace=ws)
    return {"result": result}


@router.get("/projects/{project_id}/overview")
async def overview(project_id: str):
    ws = await _get_workspace(project_id)
    result = await loomgraph.overview(workspace=ws)
    return {"result": result}
