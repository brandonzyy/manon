"""Index and status tools."""
from __future__ import annotations

from shared.ast_sync import find_project_by_repo_id, analyze_index_coverage

# Will be injected by parent
_client = None


def init(client):
    """Inject dependencies."""
    global _client
    _client = client


def register_index_tools(mcp):
    """Index trigger and status tools."""

    @mcp.tool()
    def manon_index_status(repo_id: str) -> str:
        """查看仓库的索引状态和统计信息。

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。

        Args:
            repo_id: 仓库 ID
        """
        result = _client._get(f"/api/v1/repos/{repo_id}/index-status")
        s = result["status"]
        stats = result.get("stats")
        msg = f"状态: {s}"
        if stats:
            total_files = stats.get('total_files', stats.get('files_scanned', stats.get('files_synced', 0)))
            msg += f"\n文件: {total_files}"
            msg += f"\n实体: {stats.get('total_entities', stats.get('entities_added', 0))}"
            msg += f", 关系: {stats.get('total_relations', stats.get('relations_added', 0))}"
            msg += f", 块: {stats.get('total_chunks', stats.get('chunks_added', 0))}"

        # Directory-level coverage
        found = find_project_by_repo_id(repo_id)
        if found:
            local_path, info = found
            try:
                coverage = analyze_index_coverage(
                    local_path, info.get("file_hashes", {}))
                if coverage:
                    msg += f"\n\n{coverage}"
            except Exception:
                pass

        return "<!-- DISPLAY_VERBATIM -->\n" + msg
