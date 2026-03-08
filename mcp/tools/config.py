"""Config and account tools."""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("manon-mcp")

# Will be injected by parent
_client = None
_config = None


def init(client, config):
    """Inject dependencies."""
    global _client, _config
    _client = client
    _config = config


def register_config_tools(mcp):
    """Config and account tools."""

    @mcp.tool()
    def manon_config() -> str:
        """查看当前 Manon 配置和连接状态。

        IMPORTANT: 返回结果已格式化，请原样输出给用户，不要总结或改写。
        """
        log.info("manon_config called")
        lines = [f"─── ⚙️ Manon 配置 {'─' * 28}"]
        lines.append(f"  🏷️ 版本  {_config.CLIENT_VERSION}")
        lines.append(f"  🌐 区域  {_config.REGION}")
        lines.append(f"  🔗 API   {_config.API_URL}")
        import time as _time
        t0 = _time.monotonic()
        try:
            cfg = _client._get("/api/v1/config", timeout=30)
            elapsed = _time.monotonic() - t0
            log.info("manon_config /api/v1/config OK in %.1fs", elapsed)
            lines.append(f"  💎 套餐  {cfg['tier']}")
            lines.append(f"  ⚡ 限速  {cfg['rate_limit']} req/min")
        except Exception as e:
            elapsed = _time.monotonic() - t0
            log.warning("manon_config /api/v1/config failed in %.1fs: %s", elapsed, e)
            lines.append("  ⚠️ 服务  连接超时")
        if _config._update_notice:
            lines.append(_config._update_notice)
        elif not _config._version_checked:
            threading.Thread(target=_config._check_version, daemon=True).start()
        return "<!-- DISPLAY_VERBATIM -->\n" + "\n".join(lines)

    @mcp.tool()
    def manon_account() -> str:
        """查看账户信息：套餐、配额使用情况、近 30 天用量。"""
        try:
            acc = _client._get("/api/v1/account")
        except Exception as e:
            return f"获取账户信息失败: {e}"
        q = acc["quotas"]
        lines = [
            f"套餐: {acc['tier']}",
            f"速率限制: {acc['rate_limit']} req/min",
            f"仓库: {q['repos']['used']}/{q['repos']['limit']}",
            f"深度查询 (今日): {q['deep_query_daily']['used']}/{q['deep_query_daily']['limit']}",
            f"30 天总调用: {acc['usage_30d']}",
        ]
        return "\n".join(lines)
