"""Manon MCP Server — expose code intelligence as Claude Code tools.

Supports both git-based repos (server-side clone) and local repos
(client-side AST extraction + cloud sync).

Uses core.ast for project registry and AST scanning.
Keeps sync HTTP helpers for MCP tool compatibility.

Sub-modules:
  _config  — version, geo-routing, version check
  _client  — HTTP helpers, response formatters
  _sync    — scan cache loader and batch uploader
  _hooks   — git / Claude Code hook installation
  _tools   — all 19 MCP tool definitions
"""
from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from . import _client, _config, _hooks, _sync, _tools

mcp = FastMCP("manon", instructions="""\
Manon 代码智能工具 — 语义搜索、图遍历、影响分析

DISPLAY RULES:
When a tool result contains the marker "<!-- DISPLAY_VERBATIM -->", you MUST output the ENTIRE result \
as-is to the user. Do NOT summarize, truncate, or reformat it. The content is pre-formatted for display.""")

log = logging.getLogger("manon-mcp")

# ── Log to file (MCP stdio occupies stdout/stderr) ────
_log_dir = Path.home() / ".manon"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_handler = logging.FileHandler(_log_dir / "mcp.log", encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
log.addHandler(_log_handler)
log.setLevel(logging.DEBUG)

# ── Constants ─────────────────────────────────────────
MAX_RESPONSE_CHARS = 8000
HTTP_TIMEOUT = 120  # Increased from 45 to handle slow networks

# ── Inject dependencies ──────────────────────────────
_constants = {
    "MAX_RESPONSE_CHARS": MAX_RESPONSE_CHARS,
    "HTTP_TIMEOUT": HTTP_TIMEOUT,
}

_client.init(_config, _constants)
_sync.init(_client)
_hooks.init(_config)
 

# ── Register all MCP tools ───────────────────────────
_tools.register(mcp, client=_client, sync=_sync, hooks=_hooks, config=_config)
