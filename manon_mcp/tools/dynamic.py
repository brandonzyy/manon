"""Dynamic edge merge tools."""
from __future__ import annotations

import json
from pathlib import Path

from core.ast import find_project_by_repo_id

from .deps import ToolDependencies


def register_dynamic_tools(mcp, deps: ToolDependencies):
    """Register dynamic-edge merge tools."""
    client = deps.client

    @mcp.tool()
    def manon_merge_dynamic(repo_id: str, deps_path: str = "dynamic-deps.json") -> str:
        """合并运行时追踪的动态调用边到知识图谱。"""
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
