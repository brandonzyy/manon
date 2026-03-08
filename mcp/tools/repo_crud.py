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
    """Repo create, get, delete, push-update tools."""

    @mcp.tool()
    def manon_repos_create(name: str, git_url: str = "", branch: str = "main", local_path: str = "") -> str:
        """创建新的代码仓库。支持 git URL（服务端 clone）或本地路径（客户端 AST 同步）。

        Args:
            name: 仓库名称
            git_url: Git 仓库地址（可选，服务端会自动 clone）
            branch: 分支名（默认 main）
            local_path: 本地项目路径（与 git_url 二选一，会在本地提取 AST 上传到云端）
        """
        if local_path and not git_url:
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
                f"检测到 {file_count} 个文件，请调用 manon_index {repo_id} 开始索引。"
            )
        body: dict = {"name": name, "branch": branch}
        if git_url:
            body["git_url"] = git_url
        if local_path:
            body["local_path"] = local_path
        result = _client._post("/api/v1/repos", body)
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
    def manon_push_update(repo_id: str) -> str:
        """拉取最新代码并增量重建索引。本地仓库会扫描变更文件并上传 AST。

        Args:
            repo_id: 仓库 ID
        """
        found = find_project_by_repo_id(repo_id)
        if found:
            local_path, info = found
            old_hashes = info.get("file_hashes", {})
            bg_msg = _sync._start_bg_sync(repo_id, local_path, old_hashes)
            return f"增量同步已提交后台执行。{bg_msg}"
        try:
            repo = _client._get(f"/api/v1/repos/{repo_id}")
            if repo.get("source_type") == "local":
                return (
                    f"本地项目未注册（可能 manon_init 超时未完成）。\n"
                    f"请先在项目目录执行 manon_init 注册本地项目，再调用 push_update。"
                )
        except Exception:
            pass
        result = _client._post(f"/api/v1/repos/{repo_id}/push-update", {})
        return f"更新已触发: {result['status']}。用 manon_index_status 查看进度。"
