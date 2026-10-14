"""Tool-surface profile: which tools stay registered on the MCP server.

Env switches (read once at registration time):

  MANON_TOOLS   comma-separated tool names, e.g. "search,graph" —
                explicit allowlist, wins over MANON_PROFILE
  MANON_PROFILE "full" keeps every tool; "core" (default) keeps CORE_TOOLS only

Unknown profile values fall back to full (fail-open): a typo must never
leave the server with an empty toolset.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("manon-mcp")

CORE_TOOLS = frozenset({
    "search", "graph", "deep_query", "impact", "code_health",
    "init", "repos_list",
})


def resolve_allowlist() -> set[str] | None:
    """Return the tool names to keep, or None to keep every registered tool."""
    raw = os.environ.get("MANON_TOOLS", "").strip()
    if raw:
        allow = {name.strip() for name in raw.split(",") if name.strip()}
        log.info("MANON_TOOLS override — keeping %d tools: %s", len(allow), sorted(allow))
        return allow

    profile = os.environ.get("MANON_PROFILE", "core").strip().lower()
    if profile == "core":
        log.info("MANON_PROFILE=core — keeping %d core tools: %s", len(CORE_TOOLS), sorted(CORE_TOOLS))
        return set(CORE_TOOLS)
    if profile == "full":
        log.info("MANON_PROFILE=full — keeping all tools")
        return None
    log.warning("Unknown MANON_PROFILE=%r — falling back to full", profile)
    return None


def prune_tools(mcp, allowlist: set[str] | None) -> None:
    """Unregister tools whose name is not in the allowlist (no-op when None)."""
    if allowlist is None:
        return
    for tool in list(mcp._tool_manager.list_tools()):  # mcp SDK 1.x API; recheck on SDK upgrade
        if tool.name not in allowlist:
            mcp.remove_tool(tool.name)
    kept = sorted(t.name for t in mcp._tool_manager.list_tools())
    log.info("Tool surface pruned to %d tools: %s", len(kept), kept)


def strip_schema_noise(mcp) -> None:
    """Drop pydantic-generated per-parameter "title" keys from tool schemas.

    The titles just repeat the parameter name and only inflate client context.
    """
    for tool in mcp._tool_manager.list_tools():  # mcp SDK 1.x API; recheck on SDK upgrade
        _strip_titles(tool.parameters)


def _strip_titles(node) -> None:
    if isinstance(node, dict):
        node.pop("title", None)
        for value in node.values():
            _strip_titles(value)
    elif isinstance(node, list):
        for value in node:
            _strip_titles(value)
