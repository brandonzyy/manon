"""Application service for deep-query workflows."""
from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from saas.auth import TenantContext
from saas.config import settings
from saas.metering import record_query, record_usage
from saas.quota import check_deep_query_quota
from saas.services.graph import get_graph
from saas.services.llm import llm_chat, parse_json
from saas.services.query_log import save_deep_query_log

from .repo_query_service import require_indexed_repo

log = logging.getLogger("application.deep_query_service")

_DEEPQUERY_SYSTEM = """You are a code-graph query planner.
Return JSON with:
- sub_questions
- covered
- missing
- queries
- reason

If the current context is already enough, return an empty queries list.
"""


def _hits_to_log(result) -> dict:
    return {
        "entities": [
            {
                "id": e.get("id", ""),
                "name": e.get("name", ""),
                "type": e.get("type", ""),
                "file_path": e.get("file_path", ""),
                "score": e.get("score", 0),
            }
            for e in result.entities[:20]
        ],
        "chunks": [
            {
                "id": c.get("id", ""),
                "entity": c.get("entity", ""),
                "score": c.get("score", 0),
                "content": c.get("content", ""),
            }
            for c in result.chunks[:20]
        ],
    }


async def deep_query_repo(
    repo_id: str,
    *,
    question: str,
    max_rounds: int,
    ctx: TenantContext,
) -> dict:
    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="LLM API key not configured (set SAAS_LLM_API_KEY)")

    await check_deep_query_quota(ctx)
    row = await require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])

    result = await mg.query(question, top_k=10, depth=1)
    accumulated = result.context or ""

    rounds_detail: list[dict] = [{
        "round": 0,
        "query": question,
        "entities": [e.get("id", e.get("name", "")) for e in result.entities],
        "chunks": [c.get("id", c.get("entity", "")) for c in result.chunks],
        "covered": False,
    }]
    training_rounds: list[dict] = [{
        "round": 0,
        "query": question,
        **_hits_to_log(result),
    }]
    rounds = [{"round": 0, "query": question, "context_chars": len(accumulated)}]
    parsed: dict = {}

    for i in range(max_rounds):
        analysis = None
        try:
            analysis = await llm_chat([
                {"role": "system", "content": _DEEPQUERY_SYSTEM},
                {"role": "user", "content": f"Question:\n{question}\n\nContext:\n{accumulated[:12000]}"},
            ], max_tokens=2048)
            parsed = parse_json(analysis)
        except Exception as exc:
            log.warning(
                "deep-query LLM round %d failed: %s (analysis=%r)",
                i + 1,
                exc,
                analysis[:200] if isinstance(analysis, str) else analysis,
            )
            break

        follow_ups = parsed.get("queries", [])
        if not follow_ups:
            if len(rounds_detail) == 1:
                rounds_detail[0]["covered"] = True
            break

        async def _run_query(q: str):
            return q, await mg.query(q, top_k=5, depth=1)

        results = await asyncio.gather(*[_run_query(q) for q in follow_ups[:3]])
        for q, follow_up_result in results:
            if follow_up_result.context:
                accumulated += f"\n\n## Follow-up: {q}\n{follow_up_result.context}"
            rounds_detail.append({
                "round": i + 1,
                "query": q,
                "entities": [e.get("id", e.get("name", "")) for e in follow_up_result.entities],
                "chunks": [c.get("id", c.get("entity", "")) for c in follow_up_result.chunks],
                "covered": True,
            })
            training_rounds.append({
                "round": i + 1,
                "query": q,
                **_hits_to_log(follow_up_result),
            })

        rounds.append({
            "round": i + 1,
            "queries": follow_ups,
            "context_chars": len(accumulated),
        })

    sub_questions = parsed.get("sub_questions", [])
    covered = parsed.get("covered", [])
    coverage = len(covered) / len(sub_questions) if sub_questions else 1.0

    await record_usage(ctx.tenant_id, "query.deep_query", repo_id)
    asyncio.create_task(record_query(
        ctx.tenant_id,
        repo_id,
        "deep_query",
        question,
        rounds=len(rounds),
        rounds_detail=rounds_detail,
        coverage=coverage,
    ))
    save_deep_query_log({
        "tenant_id": ctx.tenant_id,
        "repo_id": repo_id,
        "question": question,
        "rounds": training_rounds,
        "llm_analysis": parsed,
        "final_coverage": {
            "sub_questions": sub_questions,
            "covered": covered,
            "missing": parsed.get("missing", []),
            "coverage_ratio": coverage,
        },
    })

    return {
        "context": accumulated,
        "rounds": rounds,
        "sub_questions": sub_questions,
        "covered": covered,
    }

