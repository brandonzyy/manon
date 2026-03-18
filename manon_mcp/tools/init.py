"""Init and configure tools."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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


def _run_manon_update() -> str:
    """Execute git pull + install script to update Manon. Returns status message."""
    manon_dir = Path(__file__).resolve().parent.parent.parent
    is_windows = sys.platform == "win32"
    result = subprocess.run(["git", "pull"], cwd=manon_dir, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return f"❌ Git pull 失败: {result.stderr}"
    install_script = "install.bat" if is_windows else "install.sh"
    install_path = manon_dir / install_script
    if not install_path.exists():
        return f"❌ 安装脚本不存在: {install_path}"
    cmd = [str(install_path)] if is_windows else ["bash", str(install_path)]
    result = subprocess.run(cmd, cwd=manon_dir, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return f"❌ 安装失败: {result.stderr}"
    return "✅ Manon 已更新到最新版本，请重启编辑器以应用更新。"


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

    @mcp.tool()
    def manon_update() -> str:
        """更新 Manon 到最新版本。"""
        try:
            return _run_manon_update()
        except Exception as e:
            return f"❌ 更新失败: {str(e)}"
