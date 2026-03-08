"""Index and status tools."""
from __future__ import annotations

from shared.ast_sync import find_project_by_repo_id, analyze_index_coverage

# Will be injected by parent
_client = None
_sync = None


def init(client, sync):
    """Inject dependencies."""
    global _client, _sync
    _client = client
    _sync = sync


def register_index_tools(mcp):
    """Index, status, push-update, repo CRUD tools."""

    @mcp.tool()
    def manon_index(repo_id: str, incremental: bool = True) -> str:
        """触发代码索引构建。索引完成后才能进行搜索和分析。

        Args:
            repo_id: 仓库 ID
            incremental: 增量索引（默认 True），设为 False 全量重建
        """
        found = find_project_by_repo_id(repo_id)
        if not found:
            return f"本地项目未注册。请先在项目目录执行 manon_init 注册本地项目。"
        local_path, info = found
        old_hashes = {} if not incremental else info.get("file_hashes", {})
        # max_files: 0 = unlimited (full reindex), -1 = use default limit
        limit = 0 if not incremental else -1
        bg_msg = _sync._start_bg_sync(
            repo_id, local_path, old_hashes,
            max_files=limit, full_reindex=not incremental,
        )
        return f"本地索引已提交后台执行。{bg_msg}"

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
        prog = _sync._read_sync_progress(repo_id)
        if prog:
            ps = prog.get("status", "")
            pm = prog.get("message", "")
            ts = prog.get("updated_at", "")
            if ps == "syncing":
                msg += f"\n\n🔄 本地同步: {pm}"
                if _sync._is_syncing(repo_id):
                    msg += " (进行中)"
            elif ps == "done":
                msg += f"\n\n✅ 本地同步: {pm}"
            elif ps == "error":
                msg += f"\n\n❌ 本地同步失败: {pm}"
            if ts:
                msg += f"\n   更新于 {ts}"

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

    @mcp.tool()
    def manon_sync_progress(repo_id: str) -> str:
        """查看后台文件同步进度。manon_init 完成后如有后台同步任务，调用此工具获取实时进展。

        Args:
            repo_id: 仓库 ID
        """
        prog = _sync._read_sync_progress(repo_id)
        is_running = _sync._is_syncing(repo_id)

        if not prog:
            if is_running:
                return "🔄 同步刚启动，尚无详细进度。请稍后再次调用。"
            return "没有进行中的同步任务。"

        status = prog.get("status", "")
        message = prog.get("message", "")
        updated = prog.get("updated_at", "")

        if status == "done":
            return f"✅ 同步完成: {message}"
        elif status == "error":
            return f"❌ 同步失败: {message}"
        elif status == "syncing":
            result = f"🔄 {message}"
            if updated:
                result += f"\n   更新于 {updated}"
            if is_running:
                result += "\n⏳ 同步仍在进行中，请稍后再次调用此工具查看进展。"
            else:
                result += "\n⚠️ 同步线程已结束但状态未更新，可能已异常退出。"
            return result

        return f"状态: {status}, 消息: {message}"
