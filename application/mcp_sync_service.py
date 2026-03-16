"""Application services for MCP sync and local-repo workflows."""
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
