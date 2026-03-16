"""Repo CRUD tools."""
from __future__ import annotations

from application.mcp_sync_service import create_repo, delete_repo, get_repo, scan_files, upload_batch

from .deps import ToolDependencies


def register_repo_crud_tools(mcp, deps: ToolDependencies):
    """Register repo create/get/delete/scan/upload tools."""

    @mcp.tool()
    def manon_repos_create(name: str, branch: str = "main", local_path: str = "") -> str:
        """创建新的代码仓库。"""
        return create_repo(name=name, branch=branch, local_path=local_path, client=deps.client)

    @mcp.tool()
    def manon_repos_get(repo_id: str) -> str:
        """查看仓库详情。"""
        return get_repo(repo_id=repo_id, client=deps.client)

    @mcp.tool()
    def manon_repos_delete(repo_id: str) -> str:
        """删除仓库及其所有索引数据。"""
        return delete_repo(repo_id=repo_id, client=deps.client)

    @mcp.tool()
    def manon_scan_files(repo_id: str) -> str:
        """从磁盘缓存加载扫描结果到 MCP 内存。"""
        return scan_files(repo_id=repo_id, sync_module=deps.sync)

    @mcp.tool()
    def manon_upload_batch(repo_id: str) -> str:
        """从扫描缓存中取下一批文件上传到服务端。"""
        return upload_batch(repo_id=repo_id, sync_module=deps.sync)
