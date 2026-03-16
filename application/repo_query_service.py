"""Application services for repo query workflows."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import HTTPException, status

from saas.auth import TenantContext
from saas.db import get_db
from saas.metering import record_query, record_usage
from saas.models import ImpactResult, SearchResult
from saas.services.graph import get_graph

log = logging.getLogger("application.repo_query_service")


async def require_indexed_repo(repo_id: str, tenant_id: str):
    """Return repo row or raise HTTP errors for missing/unindexed repos."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM repos WHERE id = ? AND tenant_id = ?",
        (repo_id, tenant_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")
    if row["index_status"] != "done":
        raise HTTPException(400, f"repo not indexed yet (status={row['index_status']})")
    return row


async def search_repo(
    repo_id: str,
    query: str,
    *,
    top_k: int,
    depth: int,
    ctx: TenantContext,
) -> SearchResult:
    row = await require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
    result = await mg.query(query, top_k=top_k, depth=depth)
    await record_usage(ctx.tenant_id, "query.search", repo_id)
    asyncio.create_task(record_query(
        ctx.tenant_id,
        repo_id,
        "search",
        query,
        rounds_detail=[{
            "round": 0,
            "query": query,
            "entities": [e.get("id", e.get("name", "")) for e in result.entities[:20]],
            "chunks": [c.get("id", c.get("entity", "")) for c in result.chunks[:20]],
            "covered": True,
        }],
    ))
    return SearchResult(
        entities=result.entities,
        relations=result.relations,
        chunks=result.chunks,
        context=result.context,
    )


async def graph_repo(
    repo_id: str,
    symbol: str,
    *,
    depth: int,
    direction: str,
    ctx: TenantContext,
) -> SearchResult:
    row = await require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
    result = await mg.query(symbol, top_k=5, depth=depth, direction=direction)
    await record_usage(ctx.tenant_id, "query.graph", repo_id)
    return SearchResult(
        entities=result.entities,
        relations=result.relations,
        chunks=result.chunks,
        context=result.context,
    )


async def impact_repo(
    repo_id: str,
    *,
    commit: str,
    max_depth: int,
    ctx: TenantContext,
):
    row = await require_indexed_repo(repo_id, ctx.tenant_id)
    if not row["local_path"] and not row["git_url"]:
        return ImpactResult(
            commit=commit,
            risk={"level": "unknown", "reason": "local synced repos have no server-side git history"},
        )

    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
    result = mg.impact_commit(commit=commit, max_depth=max_depth)
    await record_usage(ctx.tenant_id, "query.impact", repo_id)
    return result


async def code_health_repo(
    repo_id: str,
    *,
    ctx: TenantContext,
    body: dict | None,
):
    from matrixone_graph.health import compute_graph_metrics, compute_score, scan_directory_debt

    row = await require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
    g = mg._load_graph()

    if g.entity_count == 0:
        log.warning(
            "code-health: graph empty for repo %s, kg_path=%s, graph_file_exists=%s",
            repo_id, mg.kg_path, (mg.kg_path / "graph.json").exists(),
        )

    graph_metrics = compute_graph_metrics(g)
    debt_metrics = None
    if body and body.get("debt_metrics"):
        debt_metrics = body["debt_metrics"]
    elif row["local_path"] and Path(row["local_path"]).is_dir():
        debt_metrics = scan_directory_debt(Path(row["local_path"]))

    result = compute_score(graph_metrics, debt_metrics)
    await record_usage(ctx.tenant_id, "query.code_health", repo_id)
    return result

