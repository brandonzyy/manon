"""Query endpoints — search, graph, impact, deep-query."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..metering import record_usage, record_query
from ..models import SearchResult, ImpactResult, DeepQueryRequest
from ..services.graph import get_graph
from ..services.llm import llm_chat, parse_json
from ..quota import check_deep_query_quota

log = logging.getLogger("saas.query")

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
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
    result = await mg.query(q, top_k=top_k, depth=depth)
    await record_usage(ctx.tenant_id, "query.search", repo_id)
    asyncio.create_task(record_query(
        ctx.tenant_id, repo_id, "search", q,
        rounds_detail=[{
            "round": 0, "query": q,
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


@router.get("/graph")
async def graph_traverse(
    repo_id: str,
    symbol: str = Query(..., min_length=1),
    depth: int = Query(1, ge=0, le=3),
    ctx: TenantContext = Depends(require_tenant),
):
    row = await _require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
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
    if not row["local_path"] and not row["git_url"]:
        # Local-sync repos have no git history on server
        return ImpactResult(commit=commit, risk={"level": "unknown", "reason": "本地同步仓库无服务端 git 历史，无法分析影响"})
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
    result = mg.impact_commit(commit=commit, max_depth=max_depth)
    await record_usage(ctx.tenant_id, "query.impact", repo_id)
    return result


@router.get("/code-health")
async def code_health(
    repo_id: str,
    ctx: TenantContext = Depends(require_tenant),
):
    """Compute 8-dimension code health score from the knowledge graph."""
    from matrixone_graph.health import compute_graph_metrics, compute_score, scan_directory_debt

    row = await _require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
    g = mg._load_graph()

    graph_metrics = compute_graph_metrics(g)

    # Static debt scan — only if repo has a local path on server
    debt_metrics = None
    if row["local_path"] and Path(row["local_path"]).is_dir():
        debt_metrics = scan_directory_debt(Path(row["local_path"]))

    result = compute_score(graph_metrics, debt_metrics)
    await record_usage(ctx.tenant_id, "query.code_health", repo_id)
    return result


# ── Deep Query ────────────────────────────────────────

_DEEPQUERY_SYSTEM = """你是一个代码知识图谱检索规划助手。你的任务是确保收集到的上下文能够完整回答用户的问题。

## 分析步骤

1. **拆解问题**：把用户的问题拆成具体的子问题/信息需求。
2. **逐项检查**：对每个子问题，检查已有上下文是否包含足够的代码细节（函数实现、调用链、参数、配置）。仅仅出现函数名不算覆盖，必须有实际的代码逻辑或实现细节。
3. **生成补充查询**：对未覆盖的子问题，提取最精确的查询词（函数名、类名、文件名、模块名）。优先使用已有上下文中出现但未展开的标识符。

## 关键规则

- **有 missing 就必须有 queries**：只要 missing 不为空，queries 也不能为空。
- **不要假设查不到**：即使你觉得知识图谱可能没有某个信息，也要尝试查询。
- **从上下文提取线索**：如果上下文中提到了某个类名/函数名但没有展开实现，用它作为查询词。

## 输出格式

只返回 JSON：
```json
{
  "sub_questions": ["子问题1", "子问题2"],
  "covered": ["已覆盖的子问题"],
  "missing": ["未覆盖的子问题"],
  "queries": ["查询词1", "查询词2"],
  "reason": "简要说明"
}
```

如果所有子问题都已覆盖：
```json
{"sub_questions": [...], "covered": [...], "missing": [], "queries": [], "reason": "上下文已完整覆盖所有子问题"}
```"""


@router.post("/deep-query")
async def deep_query(
    repo_id: str,
    body: DeepQueryRequest,
    ctx: TenantContext = Depends(require_tenant),
):
    """多轮迭代查询，确保上下文完整覆盖用户问题。"""
    await check_deep_query_quota(ctx)
    row = await _require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])

    # initial query
    result = await mg.query(body.question, top_k=10, depth=1)
    accumulated = result.context or ""
    rounds_detail: list[dict] = [{
        "round": 0, "query": body.question,
        "entities": [e.get("id", e.get("name", "")) for e in result.entities],
        "chunks": [c.get("id", c.get("entity", "")) for c in result.chunks],
        "covered": False,  # unknown yet, updated after first LLM analysis
    }]
    rounds = [{"round": 0, "query": body.question, "context_chars": len(accumulated)}]
    parsed: dict = {}

    # iterative refinement
    for i in range(body.max_rounds):
        try:
            analysis = await llm_chat([
                {"role": "system", "content": _DEEPQUERY_SYSTEM},
                {"role": "user", "content": f"## 用户问题\n{body.question}\n\n## 已有上下文\n{accumulated[:12000]}"},
            ], max_tokens=1024)
            parsed = parse_json(analysis)
        except Exception:
            log.warning("deep-query LLM round %d failed, stopping iteration", i + 1)
            break

        follow_ups = parsed.get("queries", [])
        if not follow_ups:
            # All covered — mark round 0 as covered if it was the only round
            if len(rounds_detail) == 1:
                rounds_detail[0]["covered"] = True
            break

        # Run follow-up queries in parallel
        async def _run_query(q: str):
            return q, await mg.query(q, top_k=5, depth=1)

        results = await asyncio.gather(*[_run_query(q) for q in follow_ups[:3]])
        for q, r in results:
            if r.context:
                accumulated += f"\n\n## 补充查询: {q}\n{r.context}"
            rounds_detail.append({
                "round": i + 1, "query": q,
                "entities": [e.get("id", e.get("name", "")) for e in r.entities],
                "chunks": [c.get("id", c.get("entity", "")) for c in r.chunks],
                "covered": True,  # GLM requested this query to fill a gap
            })

        rounds.append({
            "round": i + 1,
            "queries": follow_ups,
            "context_chars": len(accumulated),
        })

    # Compute coverage ratio
    sub_qs = parsed.get("sub_questions", [])
    covered = parsed.get("covered", [])
    coverage = len(covered) / len(sub_qs) if sub_qs else 1.0

    await record_usage(ctx.tenant_id, "query.deep_query", repo_id)
    asyncio.create_task(record_query(
        ctx.tenant_id, repo_id, "deep_query", body.question,
        rounds=len(rounds),
        rounds_detail=rounds_detail,
        coverage=coverage,
    ))
    return {
        "context": accumulated,
        "rounds": rounds,
        "sub_questions": sub_qs,
        "covered": covered,
    }
