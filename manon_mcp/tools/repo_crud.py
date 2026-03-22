"""Repo CRUD and index status tools."""
from __future__ import annotations

import json
from pathlib import Path

from core.ast import (
    analyze_index_coverage,
    count_scannable_files,
    find_project_by_repo_id,
    load_projects,
    save_projects,
    set_project,
)

from .deps import ToolDependencies
from .search import _resolve


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


def upload_coverage(*, repo_id: str, client) -> str:
    """Upload local coverage_map.json to server for TC metric calculation."""
    coverage_path = Path.home() / ".manon" / "scan_cache" / f"{repo_id}_coverage.json"
    if not coverage_path.exists():
        return json.dumps({"status": "skip", "message": "coverage_map not found, run manon-scan-tests.py first"})
    try:
        coverage_data = json.loads(coverage_path.read_text(encoding="utf-8"))
        result = client._post(f"/api/v1/repos/{repo_id}/coverage-map", coverage_data)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


def register_repo_crud_tools(mcp, deps: ToolDependencies):
    """Register repo list/create/get/delete/scan/upload tools."""
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

    @mcp.tool()
    def manon_repos_create(name: str, branch: str = "main", local_path: str = "") -> str:
        """创建新的代码仓库。"""
        return create_repo(name=name, branch=branch, local_path=local_path, client=deps.client)

    @mcp.tool()
    def manon_repos_get(repo_id: str) -> str:
        """查看仓库详情。"""
        return get_repo(repo_id=_resolve(repo_id), client=deps.client)

    @mcp.tool()
    def manon_repos_delete(repo_id: str) -> str:
        """删除仓库及其所有索引数据。"""
        return delete_repo(repo_id=_resolve(repo_id), client=deps.client)

    @mcp.tool()
    def manon_scan_files(repo_id: str) -> str:
        """从磁盘缓存加载扫描结果到 MCP 内存。"""
        return scan_files(repo_id=_resolve(repo_id), sync_module=deps.sync)

    @mcp.tool()
    def manon_upload_batch(repo_id: str) -> str:
        """从扫描缓存中取下一批文件上传到服务端。"""
        return upload_batch(repo_id=_resolve(repo_id), sync_module=deps.sync)

    @mcp.tool()
    def manon_upload_coverage(repo_id: str) -> str:
        """上传本地 coverage_map.json 到服务端，用于 TC 测试覆盖度计算。"""
        return upload_coverage(repo_id=_resolve(repo_id), client=deps.client)

    @mcp.tool()
    def manon_index_status(repo_id: str) -> str:
        """查看仓库的索引状态和统计信息。"""
        repo_id = _resolve(repo_id)
        result = client._get(f"/api/v1/repos/{repo_id}/index-status")
        status = result["status"]
        stats = result.get("stats")
        msg = f"status: {status}"
        if stats:
            total_files = stats.get("total_files", stats.get("files_scanned", stats.get("files_synced", 0)))
            msg += f"\nfiles: {total_files}"
            msg += f"\nentities: {stats.get('total_entities', stats.get('entities_added', 0))}"
            msg += f", relations: {stats.get('total_relations', stats.get('relations_added', 0))}"
            msg += f", chunks: {stats.get('total_chunks', stats.get('chunks_added', 0))}"

        found = find_project_by_repo_id(repo_id)
        if found:
            local_path, info = found
            try:
                indexed_hashes = (stats or {}).get("file_hashes") or info.get("file_hashes", {})
                coverage = analyze_index_coverage(local_path, indexed_hashes)
                if coverage:
                    msg += f"\n\n{coverage}"
            except Exception:
                pass

        return "<!-- DISPLAY_VERBATIM -->\n" + msg
