"""Init and configure tools."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import Context

from core.ast import (
    collect_directory_signals,
    get_project,
    needs_smart_analysis_refresh,
    preview_project_structure,
    set_custom_excludes,
    set_project,
    smart_analysis_signature,
)

from .deps import ToolDependencies

log = logging.getLogger("manon-mcp")


def check_version_update(manon_dir: str) -> dict | None:
    """Check if a newer version is available on GitHub (non-blocking)."""
    try:
        version_file = Path(manon_dir) / "VERSION"
        if not version_file.exists():
            return None
        local_version = version_file.read_text().strip()
        url = "https://raw.githubusercontent.com/brandonzyy/manon/master/VERSION"
        req = urllib.request.Request(url, headers={"User-Agent": "Manon-MCP"})
        with urllib.request.urlopen(req, timeout=3) as response:
            remote_version = response.read().decode("utf-8").strip()
        if remote_version != local_version:
            return {
                "update_available": True,
                "current": local_version,
                "latest": remote_version,
                "manon_dir": manon_dir,
            }
        return None
    except Exception:
        return None


def venv_python(manon_dir: Path | str) -> Path:
    """Path of the interpreter inside <manon_dir>/.venv."""
    if os.name == "nt":
        return Path(manon_dir) / ".venv" / "Scripts" / "python.exe"
    return Path(manon_dir) / ".venv" / "bin" / "python"


def resolve_scan_python() -> str:
    """Return the Python entrypoint for external scan scripts.

    Prefer an interpreter that owns Manon's dependencies — the running venv
    first, then <manon_dir>/.venv. The base interpreter comes last: it rarely
    has the tree-sitter grammars, and when it is PEP 668 managed the scan
    script cannot pip-install them either, so scans silently parse zero files.
    """
    candidates: list[str] = []
    executable = Path(sys.executable)
    if (executable.parent.parent / "pyvenv.cfg").exists():
        candidates.append(str(executable))
    candidates.append(str(venv_python(Path(__file__).resolve().parent.parent.parent)))
    candidates.append(str(executable))
    base_executable = getattr(sys, "_base_executable", "")
    if base_executable:
        candidates.append(base_executable)
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if Path(candidate).exists():
            return candidate
    return sys.executable


async def initialize_project(
    *,
    project_path: str,
    project_name: str,
    ctx: Context | None,
    client,
    config,
    read_update_status,
    init_existing_project,
    init_match_or_create,
    build_hooks_lines,
) -> str:
    """Run the full manon_init workflow and return the formatted result."""

    async def progress(pct: float, msg: str) -> None:
        if ctx:
            await ctx.report_progress(pct, 100.0, msg)
            await ctx.info(msg)

    loop = asyncio.get_running_loop()

    def sync_progress(pct: float, msg: str) -> None:
        asyncio.run_coroutine_threadsafe(progress(pct, msg), loop)

    log.info("manon_init called: path=%s, name=%s", project_path, project_name)

    await progress(5, "Checking API connectivity...")
    try:
        client._get_no_auth("/health")
        log.info("Health check OK")
        await progress(10, "API connectivity OK")
    except Exception as e:
        log.error("Health check failed: %s", e)
        return f"❌ Manon API unreachable ({config.API_URL}): {e}\n   Please ensure the saas service is running."

    lines = [f"─── 🧠 Manon v{config._get_client_version()} {'─' * 28}"]
    lines.append("\n📦 项目状态")
    lines.append("  ✅ API 连接成功")
    prev = read_update_status()
    if prev:
        lines.append(prev)

    await progress(15, "Loading project state...")
    proj = get_project(project_path)
    _need_match_or_create = False
    if proj:
        await progress(18, "Initializing existing project...")
        try:
            rid, proj_lines, graph_lines = await asyncio.to_thread(
                init_existing_project,
                project_path,
                proj,
                progress_cb=sync_progress,
            )
        except Exception as e:
            # StaleRepoError (or similar) — local registration cleared, fall through
            from .init_helpers import StaleRepoError
            if isinstance(e, StaleRepoError):
                log.warning("Stale repo detected for %s, will re-create", project_path)
                _need_match_or_create = True
            else:
                raise
    if not proj or _need_match_or_create:
        await progress(18, "Matching or creating repository...")
        result = await asyncio.to_thread(
            init_match_or_create,
            project_path,
            project_name,
            lines,
            progress_cb=sync_progress,
        )
        if isinstance(result, str):
            return result
        rid, proj_lines, graph_lines = result

    lines.extend(proj_lines)
    if graph_lines:
        lines.append("\n🕸️ 知识图谱")
        lines.extend(graph_lines)

    await progress(75, "Installing hooks...")
    hooks_lines = await asyncio.to_thread(build_hooks_lines, project_path)
    lines.extend(hooks_lines)

    manon_dir = str(Path(__file__).resolve().parent.parent.parent)
    lines.append(f"\n<!-- MANON_DIR={manon_dir} -->")
    lines.append(f"<!-- MANON_PYTHON={resolve_scan_python()} -->")

    update_info = check_version_update(manon_dir)
    if update_info:
        lines.append(f"\n<!-- UPDATE_AVAILABLE: {update_info['latest']} -->")
        lines.append(f"\n💡 检测到新版本 v{update_info['latest']} (当前: v{update_info['current']})")
        lines.append("   请询问用户是否需要更新，如果用户同意，调用 manon_update 工具进行更新。")

    proj = get_project(project_path)
    if needs_smart_analysis_refresh(project_path, proj):
        lines.append("\n<!-- SMART_ANALYSIS_NEEDED -->")
    else:
        lines.append("\n<!-- SMART_ANALYSIS_DONE -->")

    await progress(100, "Initialization complete")
    return "<!-- DISPLAY_VERBATIM -->\n" + "\n".join(lines)


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

