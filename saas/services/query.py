"""Query use cases owned by the SaaS layer."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import HTTPException, status

from ..config import settings
from ..db import get_db
from ..metering import record_query, record_usage
from ..models import ImpactResult, SearchResult
from ..quota import check_deep_query_quota
from .graph import get_graph
from .llm import llm_chat, parse_json
from .query_log import save_deep_query_log

log = logging.getLogger("saas.services.query")

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
    ctx,
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
    ctx,
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
    ctx,
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
    ctx,
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

    # Load coverage map from kg directory (uploaded by manon_upload_coverage)
    coverage_map = None
    coverage_path = mg.kg_path / "coverage_map.json"
    if coverage_path.exists():
        try:
            coverage_map = json.loads(coverage_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    graph_metrics = compute_graph_metrics(g, coverage_map=coverage_map)
    debt_metrics = None
    if body and body.get("debt_metrics"):
        debt_metrics = body["debt_metrics"]
    elif row["local_path"] and Path(row["local_path"]).is_dir():
        debt_metrics = scan_directory_debt(Path(row["local_path"]))

    result = compute_score(graph_metrics, debt_metrics)
    await record_usage(ctx.tenant_id, "query.code_health", repo_id)
    return result


async def upload_coverage_map_repo(
    repo_id: str,
    *,
    ctx,
    coverage_data: dict,
):
    row = await require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])
    coverage_path = mg.kg_path / "coverage_map.json"
    coverage_path.write_text(json.dumps(coverage_data, ensure_ascii=False), encoding="utf-8")
    summary = coverage_data.get("summary", {})
    return {"status": "ok", "covered": summary.get("covered", 0),
            "test_files": summary.get("test_files", 0),
            "test_functions": summary.get("test_functions", 0)}


async def impact_local_repo(
    repo_id: str,
    *,
    commit_info: str,
    changed_files: list[str],
    changed_symbols: list[dict],
    max_depth: int,
    ctx,
) -> dict:
    """Bulk caller resolution for client-side impact analysis.

    The client does git-diff + AST locally, then sends changed symbols here.
    We resolve all callers in one shot using the server-side graph.
    """
    row = await require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])

    sym_names = [s["name"] for s in changed_symbols[:30]]

    all_callers: list[str] = []
    affected_modules: set[str] = set()
    chains: list[str] = []

    for sym in sym_names:
        try:
            result = await mg.query(sym, top_k=5, depth=1, direction="callers")
        except Exception:
            continue
        for r in result.relations:
            src = r.get("src_id", "")
            tgt = r.get("tgt_id", "")
            kind = r.get("kind", "")
            if kind == "calls" and tgt and sym in tgt:
                all_callers.append(f"{src} --calls--> {tgt}")
                mod = ".".join(src.split(".")[:-1]) if "." in src else src
                affected_modules.add(mod)
                caller_name = src.split(".")[-1] if "." in src else src
                chains.append(f"{sym} → {caller_name}")

    await record_usage(ctx.tenant_id, "query.impact_local", repo_id)
    return {
        "commit_info": commit_info,
        "changed_files": changed_files,
        "changed_symbols": changed_symbols,
        "direct_callers": all_callers,
        "affected_modules": sorted(affected_modules),
        "propagation_chains": chains,
    }


def _parse_follow_ups(raw_follow_ups: list) -> list[str]:
    """Normalize follow-up query items from LLM response."""
    follow_ups: list[str] = []
    for item in raw_follow_ups:
        if isinstance(item, str) and item.strip():
            follow_ups.append(item.strip())
        elif isinstance(item, dict):
            val = item.get("query") or item.get("q") or item.get("text") or ""
            if isinstance(val, str) and val.strip():
                follow_ups.append(val.strip())
    return follow_ups


async def _run_query_rounds(
    mg, question: str, max_rounds: int,
    accumulated: str, rounds_detail: list, training_rounds: list, rounds: list,
) -> tuple[str, list, list, list, dict]:
    """Execute LLM-guided follow-up query rounds. Returns updated state + parsed LLM output."""
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
                i + 1, exc, analysis[:200] if isinstance(analysis, str) else analysis,
            )
            break

        follow_ups = _parse_follow_ups(parsed.get("queries", []))
        if not follow_ups:
            if len(rounds_detail) == 1:
                rounds_detail[0]["covered"] = True
            break

        async def _run_query(q: str):
            return q, await mg.query(q, top_k=5, depth=1)

        results = await asyncio.gather(*[_run_query(q) for q in follow_ups[:3]], return_exceptions=True)
        for q, follow_up_result in (r for r in results if isinstance(r, tuple)):
            if follow_up_result.context:
                accumulated += f"\n\n## Follow-up: {q}\n{follow_up_result.context}"
            rounds_detail.append({
                "round": i + 1, "query": q,
                "entities": [e.get("id", e.get("name", "")) for e in follow_up_result.entities],
                "chunks": [c.get("id", c.get("entity", "")) for c in follow_up_result.chunks],
                "covered": True,
            })
            training_rounds.append({"round": i + 1, "query": q, **_hits_to_log(follow_up_result)})
        rounds.append({"round": i + 1, "queries": follow_ups, "context_chars": len(accumulated)})

    return accumulated, rounds_detail, training_rounds, rounds, parsed


async def _record_deep_query(
    tenant_id: str, repo_id: str, question: str, rounds: list,
    rounds_detail: list, training_rounds: list, parsed: dict,
) -> None:
    """Record deep query usage and save training log."""
    sub_questions = parsed.get("sub_questions", [])
    covered = parsed.get("covered", [])
    coverage = len(covered) / len(sub_questions) if sub_questions else 1.0
    asyncio.create_task(record_query(
        tenant_id, repo_id, "deep_query", question,
        rounds=len(rounds), rounds_detail=rounds_detail, coverage=coverage,
    ))
    save_deep_query_log({
        "tenant_id": tenant_id, "repo_id": repo_id, "question": question,
        "rounds": training_rounds, "llm_analysis": parsed,
        "final_coverage": {
            "sub_questions": sub_questions, "covered": covered,
            "missing": parsed.get("missing", []), "coverage_ratio": coverage,
        },
    })
    return sub_questions, covered


async def deep_query_repo(
    repo_id: str,
    *,
    question: str,
    max_rounds: int,
    ctx,
) -> dict:
    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="LLM API key not configured (set SAAS_LLM_API_KEY)")

    await check_deep_query_quota(ctx)
    row = await require_indexed_repo(repo_id, ctx.tenant_id)
    mg = get_graph(ctx.tenant_id, row["local_path"], repo_name=row["name"])

    result = await mg.query(question, top_k=10, depth=1)
    accumulated = result.context or ""
    rounds_detail = [{"round": 0, "query": question,
                      "entities": [e.get("id", e.get("name", "")) for e in result.entities],
                      "chunks": [c.get("id", c.get("entity", "")) for c in result.chunks], "covered": False}]
    training_rounds = [{"round": 0, "query": question, **_hits_to_log(result)}]
    rounds = [{"round": 0, "query": question, "context_chars": len(accumulated)}]

    accumulated, rounds_detail, training_rounds, rounds, parsed = await _run_query_rounds(
        mg, question, max_rounds, accumulated, rounds_detail, training_rounds, rounds,
    )

    await record_usage(ctx.tenant_id, "query.deep_query", repo_id)
    sub_questions, covered = await _record_deep_query(
        ctx.tenant_id, repo_id, question, rounds, rounds_detail, training_rounds, parsed,
    )

    return {
        "context": accumulated,
        "rounds": rounds,
        "sub_questions": sub_questions,
        "covered": covered,
    }
