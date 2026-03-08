"""Manon MCP Server — expose code intelligence as Claude Code tools.

Supports both git-based repos (server-side clone) and local repos
(client-side AST extraction + cloud sync).

Uses shared.ast_sync for project registry and AST scanning.
Keeps sync HTTP helpers for MCP tool compatibility.

Sub-modules (loaded via importlib to avoid shadowing pip's `mcp` package):
  _config  — version, geo-routing, version check
  _client  — HTTP helpers, response formatters
  _sync    — background sync worker
  _hooks   — git / Claude Code hook installation
  _tools   — all 19 MCP tool definitions
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

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
INLINE_SCAN_LIMIT = 50  # must be <= SYNC_BATCH_SIZE to fit in one HTTP call

# ── Load sibling modules via importlib ────────────────
_dir = Path(__file__).parent


def _load_sibling(name: str):
    """Load a sibling module from the same directory using importlib."""
    spec = importlib.util.spec_from_file_location(
        f"manon_mcp_{name}", str(_dir / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_config = _load_sibling("_config")
_client = _load_sibling("_client")
_sync = _load_sibling("_sync")
_hooks = _load_sibling("_hooks")
_tools = _load_sibling("_tools")

# ── Inject dependencies ──────────────────────────────
_constants = {
    "MAX_RESPONSE_CHARS": MAX_RESPONSE_CHARS,
    "HTTP_TIMEOUT": HTTP_TIMEOUT,
    "INLINE_SCAN_LIMIT": INLINE_SCAN_LIMIT,
}

_client.init(_config, _constants)
_sync.init(_client, _constants)
_hooks.init(_config)
_tools.init(_client, _sync, _hooks, _config, _constants)

# ── Register all MCP tools ───────────────────────────
_tools.register(mcp)
