"""Indexing endpoints — trigger MatrixoneGraph index/update, webhook receiver."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import require_api_key
from ..db import db_pool
from matrixone_graph import MatrixoneGraph
from ..services.git import clone_or_pull

router = APIRouter(tags=["indexing"], dependencies=[Depends(require_api_key)])


@router.post("/projects/{project_id}/index")
async def index_project(project_id: str):
    async with db_pool() as db:
        row = await db.execute_fetchone("SELECT local_path FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(404, "Project not found")
    result = await MatrixoneGraph.get(row["local_path"]).index_report()
    return {"status": "indexed", "output": result}


@router.post("/projects/{project_id}/update")
async def update_project_index(project_id: str):
    async with db_pool() as db:
        row = await db.execute_fetchone("SELECT local_path FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(404, "Project not found")
    result = await MatrixoneGraph.get(row["local_path"]).index_report(incremental=True)
    return {"status": "updated", "output": result}


@router.post("/webhook/{project_id}")
async def webhook(project_id: str, request: Request):
    """Git webhook receiver — pull latest code and update index."""
    async with db_pool() as db:
        row = await db.execute_fetchone(
            "SELECT git_url, branch, local_path FROM projects WHERE id=?", (project_id,)
        )
    if not row:
        raise HTTPException(404, "Project not found")
    local_path = await clone_or_pull(project_id, row["git_url"], row["branch"])
    result = await MatrixoneGraph.get(local_path).index_report(incremental=True)
    return {"status": "webhook processed", "output": result}
