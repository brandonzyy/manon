"""Config and account tools."""
from __future__ import annotations

import logging
import threading

from .deps import ToolDependencies

log = logging.getLogger("manon-mcp")


def register_config_tools(mcp, deps: ToolDependencies):
    """Register config and account tools."""
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
