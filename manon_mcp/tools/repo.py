"""Repo list tool."""
from __future__ import annotations

from .deps import ToolDependencies


def register_repo_tools(mcp, deps: ToolDependencies):
    """Register repo-list tool."""
    client = deps.client

    @mcp.tool()
    def manon_repos_list() -> str:
        """列出当前租户的所有代码仓库及其索引状态。"""
        repos = client._get("/api/v1/repos")
        if not repos:
            return "no repos; use manon_repos_create"
        lines = []
        for repo in repos:
            icon = {"done": "+", "indexing": "~", "error": "x"}.get(repo["index_status"], "-")
            src = " [local]" if repo.get("source_type") == "local" else ""
            lines.append(f"  {icon} {repo['id']}  {repo['name']:<20s}  {repo['index_status']}{src}")
        return "\n".join(lines)
