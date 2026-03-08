"""Repo list tool."""
from __future__ import annotations

# Will be injected by parent
_client = None


def init(client):
    """Inject dependencies."""
    global _client
    _client = client


def register_repo_tools(mcp):
    """Repo list tool."""

    @mcp.tool()
    def manon_repos_list() -> str:
        """列出当前租户的所有代码仓库及其索引状态。"""
        repos = _client._get("/api/v1/repos")
        if not repos:
            return "没有仓库。用 manon_repos_create 添加。"
        lines = []
        for r in repos:
            icon = {"done": "+", "indexing": "~", "error": "x"}.get(r["index_status"], "-")
            src = " [local]" if r.get("source_type") == "local" else ""
            lines.append(f"  {icon} {r['id']}  {r['name']:<20s}  {r['index_status']}{src}")
        return "\n".join(lines)
