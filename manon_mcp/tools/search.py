"""Search, graph, and impact tools."""
from __future__ import annotations

from core.ast import find_project_by_repo_id

from .deps import ToolDependencies


def register_search_tools(mcp, deps: ToolDependencies):
    """Register search/graph/impact tools."""
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
