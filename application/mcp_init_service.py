"""Application services for MCP initialization workflows."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from mcp.server.fastmcp import Context

from core.ast import (
    get_project,
    needs_smart_analysis_refresh,
)

log = logging.getLogger("application.mcp_init_service")


def check_version_update(manon_dir: str) -> dict | None:
    """Check if a newer version is available on GitHub (non-blocking)."""
    try:
        import urllib.request
        version_file = Path(manon_dir) / "VERSION"
        if not version_file.exists():
            return None

        local_version = version_file.read_text().strip()

        # Check GitHub for latest version
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
        return None  # Silent fail, non-blocking


def resolve_scan_python() -> str:
    """Return a stable Python entrypoint for external scan scripts."""
    candidates: list[str] = []
    base_executable = getattr(sys, "_base_executable", "")
    if base_executable:
        candidates.append(base_executable)

    executable = Path(sys.executable)
    cfg_path = executable.parent.parent / "pyvenv.cfg"
    if cfg_path.exists():
        cfg_text = cfg_path.read_text(encoding="utf-8", errors="ignore")
        for line in cfg_text.splitlines():
            if line.startswith("executable = "):
                candidates.append(line.split("=", 1)[1].strip())
                break

    candidates.append(sys.executable)

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

    lines = [f"─── 🧠 Manon v{config.CLIENT_VERSION} {'─' * 28}"]
    lines.append("\n📦 项目状态")
    lines.append("  ✅ API 连接成功")
    prev = read_update_status()
    if prev:
        lines.append(prev)

    await progress(15, "Loading project state...")
    proj = get_project(project_path)
    if proj:
        await progress(18, "Initializing existing project...")
        rid, proj_lines, graph_lines = await asyncio.to_thread(
            init_existing_project,
            project_path,
            proj,
            progress_cb=sync_progress,
        )
    else:
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

    manon_dir = str(Path(__file__).resolve().parent.parent)
    lines.append(f"\n<!-- MANON_DIR={manon_dir} -->")
    lines.append(f"<!-- MANON_PYTHON={resolve_scan_python()} -->")

    # Check for version updates (non-blocking)
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
