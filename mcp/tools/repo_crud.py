"""Repo CRUD tools."""
from __future__ import annotations

import json
from pathlib import Path

from shared.ast_sync import (
    find_project_by_repo_id, set_project, count_scannable_files,
    load_projects, save_projects,
)

# Will be injected by parent
_client = None
_sync = None


def init(client, sync):
    """Inject dependencies."""
    global _client, _sync
    _client = client
    _sync = sync


def register_repo_crud_tools(mcp):
    """Repo create, get, delete, scan, and upload tools."""

    @mcp.tool()
    def manon_repos_create(name: str, branch: str = "main", local_path: str = "") -> str:
        """创建新的代码仓库。支持本地路径（客户端 AST 同步）。

        Args:
            name: 仓库名称
            branch: 分支名（默认 main）
            local_path: 本地项目路径（与 git_url 二选一，会在本地提取 AST 上传到云端）
        """
        if local_path:
            resolved = str(Path(local_path).resolve())
            if not Path(resolved).is_dir():
                return f"路径不存在: {resolved}"
            result = _client._post("/api/v1/repos", {
                "name": name, "branch": branch, "source_type": "local",
            })
            repo_id = result["id"]
            set_project(resolved, {
                "repo_id": repo_id, "name": name,
                "last_sync": "", "file_hashes": {},
            })
            file_count = count_scannable_files(resolved)
            return (
                f"仓库已创建: id={repo_id}, name={name}\n"
                f"本地路径: {resolved}\n"
                f"检测到 {file_count} 个文件，请通过 scan + upload_batch 同步索引。"
            )
        result = _client._post("/api/v1/repos", {"name": name, "branch": branch, "source_type": "local"})
        return f"仓库已创建: id={result['id']}, name={result['name']}, status={result['index_status']}"

    @mcp.tool()
    def manon_repos_get(repo_id: str) -> str:
        """查看仓库详情。

        Args:
            repo_id: 仓库 ID
        """
        result = _client._get(f"/api/v1/repos/{repo_id}")
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def manon_repos_delete(repo_id: str) -> str:
        """删除仓库及其所有索引数据。

        Args:
            repo_id: 仓库 ID
        """
        found = find_project_by_repo_id(repo_id)
        if found:
            local_path, _ = found
            data = load_projects()
            data["projects"].pop(local_path, None)
            save_projects(data)
        _client._delete(f"/api/v1/repos/{repo_id}")
        return f"仓库 {repo_id} 已删除。"

    @mcp.tool()
    def manon_scan_files(repo_id: str) -> str:
        """从磁盘缓存加载扫描结果到 MCP 内存。需先通过 Bash 运行 manon-scan.py 脚本。

        Args:
            repo_id: 仓库 ID
        """
        try:
            result = _sync.scan_files(repo_id)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

    @mcp.tool()
    def manon_upload_batch(repo_id: str) -> str:
        """从扫描缓存中取下一批文件上传到服务端。需先调用 manon_scan_files。循环调用直到 status == "done"。

        Args:
            repo_id: 仓库 ID
        """
        try:
            result = _sync.upload_next_batch(repo_id)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
