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
  tools/   — all 19 MCP tool definitions
"""
from __future__ import annotations

import datetime
import json
import logging
from functools import partial
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from . import _client, _config, _hooks, _sync
from .tools import impact, init_helpers, register_all_tools
from .tools.deps import ToolDependencies

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
_client.init(_config, {"MAX_RESPONSE_CHARS": MAX_RESPONSE_CHARS, "HTTP_TIMEOUT": HTTP_TIMEOUT})
_sync.init(_client)
_hooks.init(_config)

# ── Update status persistence ─────────────────────────
_UPDATE_STATUS_FILE = Path.home() / ".manon" / "update_status.json"


def _write_update_status(ok: bool, lines: list[str]) -> None:
    try:
        _UPDATE_STATUS_FILE.write_text(json.dumps({
            "ok": ok,
            "message": "\n".join(lines),
            "timestamp": datetime.datetime.now().isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _read_update_status() -> str | None:
    try:
        if not _UPDATE_STATUS_FILE.exists():
            return None
        data = json.loads(_UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
        _UPDATE_STATUS_FILE.unlink(missing_ok=True)
        tag = "OK" if data.get("ok") else "FAIL"
        return f"[previous background update {tag}] {data.get('message', '')}"
    except Exception:
        return None


# ── Register all MCP tools ───────────────────────────
register_all_tools(mcp, ToolDependencies(
    client=_client,
    config=_config,
    sync=_sync,
    hooks=_hooks,
    read_update_status=_read_update_status,
    init_existing_project=partial(init_helpers._init_existing_project, client=_client),
    init_match_or_create=partial(init_helpers._init_match_or_create, client=_client),
    build_hooks_lines=partial(init_helpers._build_hooks_lines, hooks=_hooks),
    local_impact=partial(impact.local_impact, client=_client),
))
