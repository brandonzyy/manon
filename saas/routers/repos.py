"""Repo CRUD — POST / GET / DELETE /api/v1/repos."""
from __future__ import annotations

import json
import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..metering import record_usage
from ..models import RepoCreate, RepoOut
from ..config import settings
from ..quota import check_repo_quota

router = APIRouter(prefix="/api/v1/repos", tags=["repos"])


def _row_to_repo(row) -> RepoOut:
    stats = json.loads(row["index_stats"]) if row["index_stats"] else None
    return RepoOut(
        id=row["id"], name=row["name"], git_url=row["git_url"],
        branch=row["branch"], local_path=row["local_path"],
        source_type=row["source_type"] if "source_type" in row.keys() else "",
        index_status=row["index_status"], index_stats=stats,
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


@router.post("", status_code=201)
async def create_repo(body: RepoCreate, ctx: TenantContext = Depends(require_tenant)):
    await check_repo_quota(ctx)
    db = await get_db()

    # Dedup: return existing repo if same name + tenant
    cur = await db.execute(
        "SELECT * FROM repos WHERE tenant_id = ? AND name = ?",
        (ctx.tenant_id, body.name),
    )
    existing = await cur.fetchone()
    if existing:
        return _row_to_repo(existing)

    repo_id = uuid.uuid4().hex[:8]
    local_path = body.local_path
    source_type = body.source_type or ""

    if source_type == "local" or not body.git_url:
        # Client-side AST sync — no clone, no local_path on server
        local_path = None

    await db.execute(
        "INSERT INTO repos (id, tenant_id, name, git_url, branch, local_path, source_type) VALUES (?,?,?,?,?,?,?)",
        (repo_id, ctx.tenant_id, body.name, body.git_url, body.branch, local_path, source_type),
    )
    await db.commit()
    await record_usage(ctx.tenant_id, "repos.create", repo_id)

    cur = await db.execute("SELECT * FROM repos WHERE id = ?", (repo_id,))
    return _row_to_repo(await cur.fetchone())


@router.get("")
async def list_repos(ctx: TenantContext = Depends(require_tenant)):
    db = await get_db()
    cur = await db.execute("SELECT * FROM repos WHERE tenant_id = ? ORDER BY created_at DESC", (ctx.tenant_id,))
    return [_row_to_repo(r) for r in await cur.fetchall()]


@router.get("/{repo_id}")
async def get_repo(repo_id: str, ctx: TenantContext = Depends(require_tenant)):
    db = await get_db()
    cur = await db.execute("SELECT * FROM repos WHERE id = ? AND tenant_id = ?", (repo_id, ctx.tenant_id))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")
    return _row_to_repo(row)


@router.delete("/{repo_id}", status_code=204)
async def delete_repo(repo_id: str, ctx: TenantContext = Depends(require_tenant)):
    db = await get_db()
    cur = await db.execute("SELECT * FROM repos WHERE id = ? AND tenant_id = ?", (repo_id, ctx.tenant_id))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")

    # cleanup local clone
    if row["local_path"] and row["git_url"]:
        p = Path(row["local_path"])
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    # cleanup index
    idx = Path(settings.index_dir) / ctx.tenant_id / row["name"]
    kg_path = idx / "kg"

    # Invalidate in-memory caches BEFORE deleting files
    try:
        from matrixone_graph.pipeline import invalidate_kg_cache
        invalidate_kg_cache(kg_path)
    except Exception:
        pass
    try:
        from matrixone_graph import MatrixoneGraph
        MatrixoneGraph._pool.pop(str(kg_path.resolve()), None)
    except Exception:
        pass

    if idx.exists():
        shutil.rmtree(idx, ignore_errors=True)

    await db.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
    await db.commit()
    await record_usage(ctx.tenant_id, "repos.delete", repo_id)
