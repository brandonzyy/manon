"""Indexing endpoints — trigger, poll status, push-update."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..metering import record_usage
from ..models import IndexTrigger, IndexStatus
from ..services.graph import get_graph
from ..services.git import clone_or_pull

router = APIRouter(prefix="/api/v1/repos/{repo_id}", tags=["indexing"])


async def _get_repo_row(repo_id: str, tenant_id: str):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM repos WHERE id = ? AND tenant_id = ?", (repo_id, tenant_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")
    return row


async def _run_index(repo_id: str, tenant_id: str, local_path: str, incremental: bool):
    """Background task: run indexing and update DB status."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE repos SET index_status = 'indexing', updated_at = datetime('now') WHERE id = ?",
            (repo_id,),
        )
        await db.commit()

        mg = get_graph(tenant_id, local_path)
        result = await mg.index(incremental=incremental)
        stats = {
            "files_scanned": result.files_scanned,
            "files_indexed": result.files_indexed,
            "entities_added": result.entities_added,
            "relations_added": result.relations_added,
            "chunks_added": result.chunks_added,
        }
        await db.execute(
            "UPDATE repos SET index_status = 'done', index_stats = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(stats), repo_id),
        )
        await db.commit()
    except Exception as exc:
        await db.execute(
            "UPDATE repos SET index_status = 'error', index_stats = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps({"error": str(exc)[:500]}), repo_id),
        )
        await db.commit()


@router.post("/index", status_code=202)
async def trigger_index(
    repo_id: str,
    body: IndexTrigger = IndexTrigger(),
    ctx: TenantContext = Depends(require_tenant),
):
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    if not row["local_path"]:
        raise HTTPException(400, "repo has no local path — create with git_url or local_path first")

    asyncio.create_task(_run_index(repo_id, ctx.tenant_id, row["local_path"], body.incremental))
    await record_usage(ctx.tenant_id, "indexing.trigger", repo_id)
    return {"repo_id": repo_id, "status": "indexing"}


@router.get("/index-status")
async def index_status(repo_id: str, ctx: TenantContext = Depends(require_tenant)):
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    stats = json.loads(row["index_stats"]) if row["index_stats"] else None
    return IndexStatus(repo_id=repo_id, status=row["index_status"], stats=stats)


@router.post("/push-update", status_code=202)
async def push_update(repo_id: str, ctx: TenantContext = Depends(require_tenant)):
    """Pull latest changes then re-index incrementally."""
    row = await _get_repo_row(repo_id, ctx.tenant_id)
    if row["git_url"]:
        local_path = await clone_or_pull(repo_id, row["git_url"], row["branch"])
        db = await get_db()
        await db.execute("UPDATE repos SET local_path = ? WHERE id = ?", (local_path, repo_id))
        await db.commit()
    else:
        local_path = row["local_path"]

    asyncio.create_task(_run_index(repo_id, ctx.tenant_id, local_path, incremental=True))
    await record_usage(ctx.tenant_id, "indexing.push_update", repo_id)
    return {"repo_id": repo_id, "status": "indexing"}
