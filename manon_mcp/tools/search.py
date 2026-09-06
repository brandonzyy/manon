"""Search, graph, impact, deep-query, and dynamic-edge tools."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from core.ast import find_project_by_repo_id, load_projects

from .deps import ToolDependencies
from ..query_state import record_query

log = logging.getLogger("manon-mcp")


def _resolve(repo_id: str) -> str:
    """Resolve repo name to UUID. If already a UUID (hex <=8), return as-is."""
    # Already looks like a UUID (short hex id)
    if len(repo_id) <= 8:
        try:
            int(repo_id, 16)
            return repo_id
        except ValueError:
            pass
    # Search projects.json by name
    for _path, info in load_projects()["projects"].items():
        if info.get("name") == repo_id:
            return info["repo_id"]
    return repo_id


def register_search_tools(mcp, deps: ToolDependencies):
    """Register search/graph/impact/deep-query tools."""
    client = deps.client

    @mcp.tool()
    def manon_search(repo_id: str, query: str, top_k: int = 10, depth: int = 1) -> str:
        """语义搜索代码库。"""
        repo_id = _resolve(repo_id)
        record_query(repo_id)
        result = client._get(f"/api/v1/repos/{repo_id}/search", q=query, top_k=top_k, depth=depth)
        if result.get("context"):
            return client._truncate(result["context"])
        if not result.get("entities") and not result.get("chunks"):
            return f"no result for '{query}'"
        return client._format_search(result)

    @mcp.tool()
    def manon_graph(repo_id: str, symbol: str, depth: int = 1, direction: str = "both") -> str:
        """查询代码符号的调用关系和依赖图。"""
        repo_id = _resolve(repo_id)
        record_query(repo_id)
        result = client._get(f"/api/v1/repos/{repo_id}/graph", symbol=symbol, depth=depth, direction=direction)
        if result.get("context"):
            return client._truncate(result["context"])
        return client._format_graph(result)

    @mcp.tool()
    def manon_impact(repo_id: str, commit: str = "HEAD", max_depth: int = 2) -> str:
        """分析某次 commit 的影响范围。"""
        repo_id = _resolve(repo_id)
        record_query(repo_id)
        found = find_project_by_repo_id(repo_id)
        if found:
            return deps.local_impact(repo_id, found[0], commit, max_depth)
        result = client._get(f"/api/v1/repos/{repo_id}/impact", commit=commit, max_depth=max_depth)
        return client._format_impact(result)

    @mcp.tool()
    def manon_deep_query(repo_id: str, question: str, max_rounds: int = 3) -> str:
        """深度查询代码知识图谱。"""
        repo_id = _resolve(repo_id)
        record_query(repo_id)
        try:
            result = client._post(
                f"/api/v1/repos/{repo_id}/deep-query",
                {"question": question, "max_rounds": max_rounds},
                timeout=30 + max_rounds * 30,
            )
        except httpx.TimeoutException:
            try:
                fallback = client._get(f"/api/v1/repos/{repo_id}/search", q=question, top_k=10, depth=1)
                context = fallback.get("context", "")
                if context:
                    return client._truncate(f"(deep-query timed out, fell back to search)\n\n{context}")
                return "deep-query timed out and fallback search returned no result"
            except Exception as exc:
                log.warning("deep-query timeout, fallback search also failed: %s", exc)
                return "deep-query timed out"
        lines = [result["context"]]
        lines.append(f"\n---\nrounds: {len(result['rounds'])}")
        if result.get("sub_questions"):
            lines.append(f"sub_questions: {', '.join(result['sub_questions'])}")
        if result.get("covered"):
            lines.append(f"covered: {', '.join(result['covered'])}")
        for round_info in result["rounds"]:
            if round_info.get("queries"):
                lines.append(f"  Round {round_info['round']}: follow-up {round_info['queries']}")
        return client._truncate("\n".join(lines))

    @mcp.tool()
    def manon_merge_dynamic(repo_id: str, deps_path: str = "dynamic-deps.json") -> str:
        """合并运行时追踪的动态调用边到知识图谱。"""
        repo_id = _resolve(repo_id)
        record_query(repo_id)
        path = Path(deps_path)
        if not path.exists() and deps_path == "dynamic-deps.json":
            alt = Path(".manon-runtime-deps.json")
            if alt.exists():
                path = alt
        if not path.exists():
            return f"file not found: {path.resolve()}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"failed to read {path.name}: {exc}"
        if not data:
            return f"{path.name} is empty"

        if isinstance(data, list):
            found = find_project_by_repo_id(repo_id)
            project_root = found[0] if found else str(Path.cwd())
            body = {"raw_edges": data, "project_root": project_root}
            fmt = "js-ts-paths"
            count = len(data)
        elif isinstance(data, dict):
            body = {"edges": data}
            fmt = "python-entity-ids"
            count = len(data)
        else:
            return f"unsupported format: {type(data).__name__}"

        try:
            result = client._post(f"/api/v1/repos/{repo_id}/merge-dynamic", body, timeout=30)
        except Exception as exc:
            return f"merge failed: {exc}"

        lines = [
            "dynamic edges merged",
            f"  format: {fmt}",
            f"  source: {path.name} ({count})",
            f"  added: {result.get('added', 0)}",
            f"  removed: {result.get('removed', 0)}",
            f"  skipped: {result.get('skipped', 0)}",
        ]
        resolved = result.get("resolved_from_raw", 0)
        if resolved:
            lines.append(f"  resolved from paths: {resolved}")
        return "\n".join(lines)
