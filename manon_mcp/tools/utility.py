"""Config, account, usage, embedding, and update tools."""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path

from .deps import ToolDependencies

log = logging.getLogger("manon-mcp")


def register_utility_tools(mcp, deps: ToolDependencies):
    """Register config, account, usage, embedding, and update tools."""
    client = deps.client
    config = deps.config

    @mcp.tool()
    def manon_config() -> str:
        """查看当前 Manon 配置和连接状态。"""
        log.info("manon_config called")
        lines = [f"=== Manon Config {'=' * 28}"]
        lines.append(f"  Version  {config.CLIENT_VERSION}")
        lines.append(f"  Region   {config.REGION}")
        lines.append(f"  API      {config.API_URL}")
        import time as _time
        t0 = _time.monotonic()
        try:
            cfg = client._get("/api/v1/config", timeout=30)
            elapsed = _time.monotonic() - t0
            log.info("manon_config /api/v1/config OK in %.1fs", elapsed)
            lines.append(f"  Tier     {cfg['tier']}")
            lines.append(f"  Limit    {cfg['rate_limit']} req/min")
        except Exception as exc:
            elapsed = _time.monotonic() - t0
            log.warning("manon_config /api/v1/config failed in %.1fs: %s", elapsed, exc)
            lines.append("  Service  timeout")
        if config._update_notice:
            lines.append(config._update_notice)
        elif not config._version_checked:
            threading.Thread(target=config._check_version, daemon=True).start()
        return "<!-- DISPLAY_VERBATIM -->\n" + "\n".join(lines)

    @mcp.tool()
    def manon_account() -> str:
        """查看账户信息：套餐、配额使用情况、近 30 天用量。"""
        try:
            acc = client._get("/api/v1/account")
        except Exception as exc:
            return f"Failed to fetch account info: {exc}"
        quotas = acc["quotas"]
        lines = [
            f"Tier: {acc['tier']}",
            f"Rate limit: {acc['rate_limit']} req/min",
            f"Repos: {quotas['repos']['used']}/{quotas['repos']['limit']}",
            f"Deep query today: {quotas['deep_query_daily']['used']}/{quotas['deep_query_daily']['limit']}",
            f"Usage 30d: {acc['usage_30d']}",
        ]
        return "\n".join(lines)

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
