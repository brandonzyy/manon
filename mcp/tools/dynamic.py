"""Dynamic edge merge tools."""
from __future__ import annotations

import json
from pathlib import Path

from shared.ast_sync import find_project_by_repo_id

# Will be injected by parent
_client = None


def init(client):
    """Inject dependencies."""
    global _client
    _client = client


def register_dynamic_tools(mcp):
    """Dynamic edge merge tools."""

    @mcp.tool()
    def manon_merge_dynamic(repo_id: str, deps_path: str = "dynamic-deps.json") -> str:
        """合并运行时追踪的动态调用边到知识图谱。

        支持两种格式（自动检测）：
        - Python 格式: {"caller->callee": count} — 由 pytest --trace-calls 生成
        - JS/TS 格式: [{"from": path, "to": path}] — 由 Module._load hook 生成

        动态边使用 file_path="__dynamic__" 标记，不会与静态 AST 边冲突。

        Args:
            repo_id: 仓库 ID
            deps_path: 动态依赖文件路径（默认 dynamic-deps.json，也支持 .manon-runtime-deps.json）
        """
        # Try multiple default paths
        p = Path(deps_path)
        if not p.exists() and deps_path == "dynamic-deps.json":
            alt = Path(".manon-runtime-deps.json")
            if alt.exists():
                p = alt
        if not p.exists():
            return f"文件不存在: {p.resolve()}\n请先运行 pytest --trace-calls 或 vitest 生成依赖文件"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return f"读取 {p.name} 失败: {e}"
        if not data:
            return f"{p.name} 为空，没有动态边可合并。"

        # Auto-detect format: list = raw file-path edges, dict = pre-resolved
        body: dict = {}
        if isinstance(data, list):
            # JS/TS raw format: [{"from": ..., "to": ...}]
            found = find_project_by_repo_id(repo_id)
            project_root = found[0] if found else str(Path.cwd())
            body = {"raw_edges": data, "project_root": project_root}
            fmt = "JS/TS 文件路径"
            count = len(data)
        elif isinstance(data, dict):
            body = {"edges": data}
            fmt = "Python 实体 ID"
            count = len(data)
        else:
            return f"不支持的格式: {type(data).__name__}，期望 dict 或 list"

        try:
            result = _client._post(
                f"/api/v1/repos/{repo_id}/merge-dynamic",
                body,
                timeout=30,
            )
            added = result.get("added", 0)
            removed = result.get("removed", 0)
            skipped = result.get("skipped", 0)
            resolved = result.get("resolved_from_raw", 0)
            lines = [
                "动态边合并完成。",
                f"  格式: {fmt}  来源: {p.name} ({count} 条)",
                f"  添加: {added}  移除旧边: {removed}  跳过: {skipped}",
            ]
            if resolved:
                lines.append(f"  路径解析: {resolved} 条边从文件路径转换为实体 ID")
            return "\n".join(lines)
        except Exception as e:
            return f"合并失败: {e}"
