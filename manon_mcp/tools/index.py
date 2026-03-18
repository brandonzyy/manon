"""Index status tools."""
from __future__ import annotations

from core.ast import analyze_index_coverage, find_project_by_repo_id

from .deps import ToolDependencies


def register_index_tools(mcp, deps: ToolDependencies):
    """Register index status tools."""
    client = deps.client

    @mcp.tool()
    def manon_index_status(repo_id: str) -> str:
        """查看仓库的索引状态和统计信息。"""
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
                # 优先用服务端返回的实际入图文件哈希
                indexed_hashes = (stats or {}).get("file_hashes") or info.get("file_hashes", {})
                coverage = analyze_index_coverage(local_path, indexed_hashes)
                if coverage:
                    msg += f"\n\n{coverage}"
            except Exception:
                pass

        return "<!-- DISPLAY_VERBATIM -->\n" + msg
