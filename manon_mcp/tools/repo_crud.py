"""Repo CRUD tools."""
from __future__ import annotations

import json
from pathlib import Path

from core.ast import (
    count_scannable_files,
    find_project_by_repo_id,
    load_projects,
    save_projects,
    set_project,
)

from .deps import ToolDependencies


def create_repo(*, name: str, branch: str, local_path: str, client) -> str:
    """Create a local Manon repo and optionally attach a local project path."""
    if local_path:
        resolved = str(Path(local_path).resolve())
        if not Path(resolved).is_dir():
            return f"路径不存在: {resolved}"
        result = client._post("/api/v1/repos", {
            "name": name,
            "branch": branch,
            "source_type": "local",
        })
        repo_id = result["id"]
        set_project(resolved, {
            "repo_id": repo_id,
            "name": name,
            "last_sync": "",
            "file_hashes": {},
        })
        file_count = count_scannable_files(resolved)
        return (
            f"仓库已创建: id={repo_id}, name={name}\n"
            f"本地路径: {resolved}\n"
            f"检测到 {file_count} 个文件，请通过 scan + upload_batch 同步索引。"
        )
    result = client._post("/api/v1/repos", {
        "name": name,
        "branch": branch,
        "source_type": "local",
    })
    return f"仓库已创建: id={result['id']}, name={result['name']}, status={result['index_status']}"


def get_repo(*, repo_id: str, client) -> str:
    """Return repo details as JSON."""
    result = client._get(f"/api/v1/repos/{repo_id}")
    return json.dumps(result, indent=2, ensure_ascii=False)


def delete_repo(*, repo_id: str, client) -> str:
    """Delete a repo and remove local project binding if present."""
    found = find_project_by_repo_id(repo_id)
    if found:
        local_path, _ = found
        data = load_projects()
        data["projects"].pop(local_path, None)
        save_projects(data)
    client._delete(f"/api/v1/repos/{repo_id}")
    return f"仓库 {repo_id} 已删除。"


def scan_files(*, repo_id: str, sync_module) -> str:
    """Load scan cache and return JSON status."""
    try:
        result = sync_module.scan_files(repo_id)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


def upload_batch(*, repo_id: str, sync_module) -> str:
    """Upload one cached sync batch and return JSON status."""
    try:
        result = sync_module.upload_next_batch(repo_id)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


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
