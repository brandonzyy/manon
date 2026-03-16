"""Query endpoints — search, graph, impact, deep-query."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from application.deep_query_service import deep_query_repo
from application.repo_query_service import (
    code_health_repo,
    graph_repo,
    impact_repo,
    search_repo,
)
from ..auth import TenantContext, require_tenant
from ..models import DeepQueryRequest

router = APIRouter(prefix="/api/v1/repos/{repo_id}", tags=["query"])


@router.get("/search")
async def search(
    repo_id: str,
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    depth: int = Query(1, ge=0, le=3),
    ctx: TenantContext = Depends(require_tenant),
):
    return await search_repo(repo_id, q, top_k=top_k, depth=depth, ctx=ctx)


@router.get("/graph")
async def graph_traverse(
    repo_id: str,
    symbol: str = Query(..., min_length=1),
    depth: int = Query(1, ge=0, le=3),
    direction: str = Query("both", pattern="^(both|callers|callees)$"),
    ctx: TenantContext = Depends(require_tenant),
):
    return await graph_repo(repo_id, symbol, depth=depth, direction=direction, ctx=ctx)


@router.get("/impact")
async def impact_analysis(
    repo_id: str,
    commit: str = Query("HEAD"),
    max_depth: int = Query(2, ge=1, le=5),
    ctx: TenantContext = Depends(require_tenant),
):
    return await impact_repo(repo_id, commit=commit, max_depth=max_depth, ctx=ctx)


@router.post("/code-health")
@router.get("/code-health")
async def code_health(
    repo_id: str,
    ctx: TenantContext = Depends(require_tenant),
    body: dict | None = Body(None),
):
    return await code_health_repo(repo_id, ctx=ctx, body=body)


@router.post("/deep-query")
async def deep_query(
    repo_id: str,
    body: DeepQueryRequest,
    ctx: TenantContext = Depends(require_tenant),
):
    return await deep_query_repo(
        repo_id,
        question=body.question,
        max_rounds=body.max_rounds,
        ctx=ctx,
    )
