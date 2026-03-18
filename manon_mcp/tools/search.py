"""Search, graph, impact, and deep-query tools."""
from __future__ import annotations

import logging

import httpx

from core.ast import find_project_by_repo_id

from .deps import ToolDependencies

log = logging.getLogger("manon-mcp")


def register_search_tools(mcp, deps: ToolDependencies):
    """Register search/graph/impact/deep-query tools."""
    client = deps.client

    @mcp.tool()
    def manon_search(repo_id: str, query: str, top_k: int = 10, depth: int = 1) -> str:
        """语义搜索代码库。"""
        result = client._get(f"/api/v1/repos/{repo_id}/search", q=query, top_k=top_k, depth=depth)
        if result.get("context"):
            return client._truncate(result["context"])
        if not result.get("entities") and not result.get("chunks"):
            return f"no result for '{query}'"
        return client._format_search(result)

    @mcp.tool()
    def manon_graph(repo_id: str, symbol: str, depth: int = 1, direction: str = "both") -> str:
        """查询代码符号的调用关系和依赖图。"""
        result = client._get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth, direction=direction)
        if result.get("context"):
            return client._truncate(result["context"])
        return client._format_graph(result)

    @mcp.tool()
    def manon_impact(repo_id: str, commit: str = "HEAD", max_depth: int = 2) -> str:
        """分析某次 commit 的影响范围。"""
        found = find_project_by_repo_id(repo_id)
        if found:
            return deps.local_impact(repo_id, found[0], commit, max_depth)
        result = client._get(f"/api/v1/repos/{repo_id}/impact", commit=commit, max_depth=max_depth)
        return client._format_impact(result)

    @mcp.tool()
    def manon_deep_query(repo_id: str, question: str, max_rounds: int = 3) -> str:
        """深度查询代码知识图谱。"""
        try:
            result = client._post(
                f"/api/v1/repos/{repo_id}/deep-query",
                {"question": question, "max_rounds": max_rounds},
                timeout=30 + max_rounds * 30,
            )
        except httpx.TimeoutException:
            try:
                fallback = client._get(f"/api/v1/repos/{repo_id}/search", q=question, top_k=10, depth=1)
                context = fallback.get("context", "")
                if context:
                    return client._truncate(f"(deep-query timed out, fell back to search)\n\n{context}")
                return "deep-query timed out and fallback search returned no result"
            except Exception as exc:
                log.warning("deep-query timeout, fallback search also failed: %s", exc)
                return "deep-query timed out"
        lines = [result["context"]]
        lines.append(f"\n---\nrounds: {len(result['rounds'])}")
        if result.get("sub_questions"):
            lines.append(f"sub_questions: {', '.join(result['sub_questions'])}")
        if result.get("covered"):
            lines.append(f"covered: {', '.join(result['covered'])}")
        for round_info in result["rounds"]:
            if round_info.get("queries"):
                lines.append(f"  Round {round_info['round']}: follow-up {round_info['queries']}")
        return client._truncate("\n".join(lines))
