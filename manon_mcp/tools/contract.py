"""Contract audit tool.

Runs entirely on the local tree. Unlike the health dimensions it needs no server
round trip, because the facts it computes are not in the graph: the graph never
saw the config files, the SQL literals, or the tool scripts.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.ast import find_project_by_repo_id

from .deps import ToolDependencies
from .search import _resolve

log = logging.getLogger("manon-mcp")


def register_contract_tools(mcp, deps: ToolDependencies):
    """Register the contract audit tool."""

    @mcp.tool()
    def manon_contract_audit(repo_id: str, tables: str = "", limit: int = 8) -> str:
        """契约对账：端点/配置/状态值/守卫包络四张表，找出图谱看不见的死面。"""
        from core.contract_audit import TABLES, audit_project
        from core.contract_audit.report import render

        repo_id = _resolve(repo_id)
        found = find_project_by_repo_id(repo_id)
        if not found:
            return f"repo {repo_id} 未在本机注册，契约对账需要本地源码树"
        local_path = found[0]
        if not Path(local_path).is_dir():
            return f"本地路径不存在: {local_path}"

        requested = tuple(t.strip() for t in tables.split(",") if t.strip()) or TABLES
        unknown = [t for t in requested if t not in TABLES]
        if unknown:
            return f"未知的表: {', '.join(unknown)}；可选: {', '.join(TABLES)}"

        try:
            result = audit_project(local_path, tables=requested)
        except Exception as exc:
            log.warning("contract audit failed for %s: %s", local_path, exc)
            return f"契约对账失败: {exc}"

        body = render(result, limit=limit)
        if not result["policy_source"]:
            body += (
                "\n\n提示：建仓根 .manon-contract.yaml 记录豁免（哪条死面是"
                "刻意保留的运维口），否则每轮都会重报同样的条目。"
            )
        return "<!-- DISPLAY_VERBATIM -->\n" + body
