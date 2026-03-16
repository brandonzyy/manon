"""Health and hooks tools."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from core.ast import find_project_by_repo_id

from .deps import ToolDependencies

log = logging.getLogger("manon-mcp")

_debt_cache: dict[str, dict] = {}
_debt_lock = threading.Lock()


def _scan_debt_locally(repo_id: str) -> dict | None:
    """Compute TD metrics locally where source files are available."""
    found = find_project_by_repo_id(repo_id)
    if not found:
        return None
    project_path, _ = found
    repo_path = Path(project_path)
    if not repo_path.is_dir():
        return None
    try:
        from matrixone_graph.health import scan_directory_debt

        return scan_directory_debt(repo_path)
    except Exception as exc:
        log.warning("Local debt scan failed: %s", exc)
        return None


def _scan_debt_background(repo_id: str) -> None:
    """Run debt scan in a background thread and cache the result."""
    try:
        result = _scan_debt_locally(repo_id)
        if result:
            with _debt_lock:
                _debt_cache[repo_id] = result
            log.info("Background debt scan done for %s", repo_id)
    except Exception as exc:
        log.warning("Background debt scan failed: %s", exc)


def _get_cached_debt(repo_id: str) -> dict | None:
    """Return cached debt metrics if available, and trigger a background refresh."""
    with _debt_lock:
        cached = _debt_cache.get(repo_id)
    threading.Thread(target=_scan_debt_background, args=(repo_id,), daemon=True).start()
    return cached


def register_health_tools(mcp, deps: ToolDependencies):
    """Register health and hook tools."""
    client = deps.client
    hooks = deps.hooks

    @mcp.tool()
    def manon_code_health(repo_id: str) -> str:
        """分析代码库的健康状况。"""
        debt = _get_cached_debt(repo_id)
        body = {"debt_metrics": debt} if debt else {}
        result = client._post(f"/api/v1/repos/{repo_id}/code-health", body, timeout=15)
        score = result.get("score", 0)
        dims = result.get("dimensions", [])
        grade = result.get(
            "grade",
            "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D",
        )

        lines = [f"代码健康评分: {score}/100 ({grade})"]
        lines.append(
            f"实体: {result.get('entity_count', 0)}, 关系: {result.get('relation_count', 0)}"
        )
        if not result.get("reliable", True):
            lines.append("warning: graph data is empty, score may be unreliable")
        lines.append("")

        for dimension in dims:
            value = dimension["value"]
            bar = "#" * value + "-" * (10 - value)
            lines.append(
                f"  {dimension['abbr']:>2s} {dimension['name']:<6s} "
                f"{bar} {value}/10 (weight {dimension['weight']})"
            )
            detail = dimension.get("detail", {})
            if detail:
                info = ", ".join(
                    f"{key}={val}"
                    for key, val in detail.items()
                    if not isinstance(val, list)
                )
                if info:
                    lines.append(f"     {info}")

        return "<!-- DISPLAY_VERBATIM -->\n" + "\n".join(lines)

    @mcp.tool()
    def manon_setup_hooks(project_path: str) -> str:
        """为项目安装 git pre-push hook。"""
        resolved = Path(project_path).resolve()
        if not (resolved / ".git").is_dir():
            return f"not a git repo: {resolved}"

        result = hooks._install_hook(project_path)
        hooks._persist_api_config()
        if result:
            return f"{result}\ngit push will now refresh the graph and print code health"
        return "pre-push hook already exists; API config refreshed"
