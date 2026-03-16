"""Usage, embedding, and update tools."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .deps import ToolDependencies


def register_utility_tools(mcp, deps: ToolDependencies):
    """Register usage, embedding, and update tools."""
    client = deps.client
    config = deps.config

    @mcp.tool()
    def manon_usage(days: int = 30) -> str:
        """查看 API 用量统计。"""
        result = client._get("/api/v1/usage", days=days)
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def manon_embedding(texts: list[str]) -> str:
        """将文本转换为向量嵌入。"""
        result = client._post("/api/v1/embedding", {"inputs": texts})
        return f"generated {result['count']} vectors (dimension: {len(result['embeddings'][0])})"

    @mcp.tool()
    def manon_update() -> str:
        """检查并更新 Manon 到最新版本。"""
        install_dir = Path(__file__).resolve().parent.parent.parent
        parts: list[str] = []
        prev = deps.read_update_status()
        if prev:
            parts.append(prev)
        try:
            branch = config._git_branch()
            subprocess.run(
                ["git", "fetch", "--quiet", "origin", branch],
                cwd=str(install_dir),
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=5,
            )
            behind = subprocess.run(
                ["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
                cwd=str(install_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                stdin=subprocess.DEVNULL,
                timeout=3,
            ).stdout.strip()
            if behind and int(behind) > 0:
                parts.append(
                    f"found {behind} newer commits (current {config.CLIENT_VERSION})\n"
                    f"run:\n  python {install_dir / 'scripts' / 'manon-update.py'}"
                )
            else:
                parts.append(f"current version {config.CLIENT_VERSION} is up to date")
        except Exception:
            parts.append(
                f"current version: {config.CLIENT_VERSION}\n"
                f"unable to check update automatically\n"
                f"run:\n  python {install_dir / 'scripts' / 'manon-update.py'}"
            )
        return "\n".join(parts)
