"""Health, hooks tools."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from shared.ast_sync import find_project_by_repo_id

log = logging.getLogger("manon-mcp")

# Will be injected by parent
_client = None
_hooks = None

# Cached debt metrics from background scan
_debt_cache: dict[str, dict] = {}
_debt_lock = threading.Lock()


def init(client, hooks):
    """Inject dependencies."""
    global _client, _hooks
    _client = client
    _hooks = hooks


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
    except Exception as e:
        log.warning("Local debt scan failed: %s", e)
        return None


def _scan_debt_background(repo_id: str) -> None:
    """Run debt scan in a background thread and cache the result."""
    try:
        result = _scan_debt_locally(repo_id)
        if result:
            with _debt_lock:
                _debt_cache[repo_id] = result
            log.info("Background debt scan done for %s", repo_id)
    except Exception as e:
        log.warning("Background debt scan failed: %s", e)


def _get_cached_debt(repo_id: str) -> dict | None:
    """Return cached debt metrics if available, and trigger a background refresh."""
    with _debt_lock:
        cached = _debt_cache.get(repo_id)
    # Always refresh in background for next call
    t = threading.Thread(target=_scan_debt_background, args=(repo_id,), daemon=True)
    t.start()
    return cached


def register_health_tools(mcp):
    """Health, hooks, and dynamic merge tools."""

    @mcp.tool()
    def manon_code_health(repo_id: str) -> str:
        """分析代码库的健康状况。基于知识图谱计算 8 个维度的健康评分。

        维度: 模块耦合度(MC)、循环依赖(CD)、扇入集中度(FI)、死代码(DC)、
              测试覆盖(TC)、函数规模(FS)、技术债务(TD)、继承深度(ID)

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。

        Args:
            repo_id: 仓库 ID（从 manon_repos_list 获取）
        """
        # Use cached debt (non-blocking); background thread refreshes for next call
        debt = _get_cached_debt(repo_id)
        body = {"debt_metrics": debt} if debt else {}
        result = _client._post(f"/api/v1/repos/{repo_id}/code-health", body, timeout=15)
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
            lines.append("⚠ 图谱数据为空，评分不可靠。请先同步文件并重建索引。")
        lines.append("")

        for dimension in dims:
            value = dimension["value"]
            bar = "█" * value + "░" * (10 - value)
            lines.append(
                f"  {dimension['abbr']:>2s} {dimension['name']:<6s} "
                f"{bar} {value}/10 (权重{dimension['weight']})"
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
        """为项目安装 git pre-push hook，push 后自动更新知识图谱并输出代码健康评分。

        Args:
            project_path: 项目在本机的绝对路径
        """
        resolved = Path(project_path).resolve()
        if not (resolved / ".git").is_dir():
            return f"不是 git 仓库: {resolved}"

        result = _hooks._install_hook(project_path)
        _hooks._persist_api_config()
        if result:
            return f"{result}\ngit push 后将自动更新知识图谱并输出代码健康评分。"
        return "pre-push hook 已存在，API 配置已更新。"
