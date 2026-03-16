"""Init and configure tools."""
from __future__ import annotations

import json

from mcp.server.fastmcp import Context

from application.mcp_init_service import initialize_project
from core.ast import (
    collect_directory_signals,
    get_project,
    preview_project_structure,
    set_custom_excludes,
    set_project,
    smart_analysis_signature,
)

from .deps import ToolDependencies


def register_init_tools(mcp, deps: ToolDependencies):
    """Register init/configure tools."""

    @mcp.tool()
    async def manon_init(project_path: str, project_name: str = "", ctx: Context = None) -> str:
        """初始化当前项目的 Manon 连接。"""
        return await initialize_project(
            project_path=project_path,
            project_name=project_name,
            ctx=ctx,
            client=deps.client,
            config=deps.config,
            read_update_status=deps.read_update_status,
            init_existing_project=deps.init_existing_project,
            init_match_or_create=deps.init_match_or_create,
            build_hooks_lines=deps.build_hooks_lines,
        )

    @mcp.tool()
    def manon_directory_signals(project_path: str) -> str:
        """获取项目目录结构信号。"""
        signals = collect_directory_signals(project_path)
        return json.dumps(signals, ensure_ascii=False, indent=2)

    @mcp.tool()
    def manon_configure_excludes(project_path: str, exclude_patterns: list[str]) -> str:
        """为项目设置自定义排除模式。"""
        proj = get_project(project_path)
        if not proj:
            return "project not registered; run manon_init first"
        set_custom_excludes(project_path, exclude_patterns)
        proj = get_project(project_path)
        if proj:
            proj["smart_analysis_done"] = True
            proj["smart_analysis_signature"] = smart_analysis_signature(project_path)
            set_project(project_path, proj)
        preview = preview_project_structure(project_path)
        return f"configured {len(exclude_patterns)} custom exclude patterns\n\nupdated structure:\n{preview}"
