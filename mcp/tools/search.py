"""Search, graph, and impact tools."""
from __future__ import annotations

from shared.ast_sync import find_project_by_repo_id

# Will be injected by parent
_client = None
_local_impact = None


def init(client, local_impact_func):
    """Inject dependencies."""
    global _client, _local_impact
    _client = client
    _local_impact = local_impact_func


def register_search_tools(mcp):
    """Search, graph, and impact tools."""

    @mcp.tool()
    def manon_search(repo_id: str, query: str, top_k: int = 10, depth: int = 1) -> str:
        """语义搜索代码库。用自然语言描述你要找的内容，返回相关的代码实体、关系和上下文。

        Args:
            repo_id: 仓库 ID（从 manon_repos_list 获取）
            query: 搜索内容，如 "用户认证流程"、"数据库连接池"
            top_k: 返回结果数量（默认 10）
            depth: 图遍历深度（默认 1）
        """
        result = _client._get(f"/api/v1/repos/{repo_id}/search", q=query, top_k=top_k, depth=depth)
        if result.get("context"):
            return _client._truncate(result["context"])
        if not result.get("entities") and not result.get("chunks"):
            return f"未找到与 '{query}' 相关的结果。"
        return _client._format_search(result)

    @mcp.tool()
    def manon_graph(repo_id: str, symbol: str, depth: int = 1, direction: str = "both") -> str:
        """查询代码符号的调用关系和依赖图。

        Args:
            repo_id: 仓库 ID
            symbol: 代码符号名，如 "UserService"、"authenticate"
            depth: 遍历深度（默认 1，最大 3）
            direction: 遍历方向 - "both"(双向), "callers"(只查上游调用者), "callees"(只查下游被调用者)
        """
        result = _client._get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth, direction=direction)
        if result.get("context"):
            return _client._truncate(result["context"])
        return _client._format_graph(result)

    @mcp.tool()
    def manon_impact(repo_id: str, commit: str = "HEAD", max_depth: int = 2) -> str:
        """分析某次 commit 的影响范围。返回变更的符号、直接/间接调用者、受影响模块和风险评估。

        Args:
            repo_id: 仓库 ID
            commit: commit hash（默认 HEAD）
            max_depth: 影响传播深度（默认 2）
        """
        found = find_project_by_repo_id(repo_id)
        if found:
            return _local_impact(repo_id, found[0], commit, max_depth)
        result = _client._get(f"/api/v1/repos/{repo_id}/impact", commit=commit, max_depth=max_depth)
        return _client._format_impact(result)
