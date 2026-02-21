"""Query endpoints — search, graph, impact."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..metering import record_usage
from ..models import SearchResult, ImpactResult
from ..services.graph import get_graph

router = APIRouter(prefix="/api/v1/repos/{repo_id}", tags=["query"])


async def _require_indexed_repo(repo_id: str, tenant_id: str):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM repos WHERE id = ? AND tenant_id = ?", (repo_id, tenant_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")
    if row["index_status"] != "done":
        raise HTTPException(400, f"repo not indexed yet (status={row['index_status']})")
    return row


@router.get("/search")
async def search(
    repo_id: str,
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    depth: int = Query(1, ge=0, le=3),
    ctx: TenantContext = Depends(require_tenant),
):
    row = await _require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"])
    result = await mg.query(q, top_k=top_k, depth=depth)
    await record_usage(ctx.tenant_id, "query.search", repo_id)
    return SearchResult(
        entities=result.entities,
        relations=result.relations,
        chunks=result.chunks,
        context=result.context,
    )


@router.get("/graph")
async def graph_traverse(
    repo_id: str,
    symbol: str = Query(..., min_length=1),
    depth: int = Query(1, ge=0, le=3),
    ctx: TenantContext = Depends(require_tenant),
):
    row = await _require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"])
    result = await mg.query(symbol, top_k=5, depth=depth)
    await record_usage(ctx.tenant_id, "query.graph", repo_id)
    return SearchResult(
        entities=result.entities,
        relations=result.relations,
        chunks=result.chunks,
        context=result.context,
    )


@router.get("/impact")
async def impact_analysis(
    repo_id: str,
    commit: str = Query("HEAD"),
    max_depth: int = Query(2, ge=1, le=5),
    ctx: TenantContext = Depends(require_tenant),
):
    row = await _require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"])
    result = mg.impact_commit(commit=commit, max_depth=max_depth)
    await record_usage(ctx.tenant_id, "query.impact", repo_id)
    return result
