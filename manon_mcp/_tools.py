"""Manon MCP tool registration coordinator."""
from __future__ import annotations

import datetime
import json
from functools import partial
from pathlib import Path

from . import tools
from .tools import impact, init_helpers
from .tools.deps import ToolDependencies

_UPDATE_STATUS_FILE = Path.home() / ".manon" / "update_status.json"


def _write_update_status(ok: bool, lines: list[str]) -> None:
    """Persist update result so next manon_update/init can report it."""
    try:
        _UPDATE_STATUS_FILE.write_text(json.dumps({
            "ok": ok,
            "message": "\n".join(lines),
            "timestamp": datetime.datetime.now().isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _read_update_status() -> str | None:
    """Read and clear previous background update result."""
    try:
        if not _UPDATE_STATUS_FILE.exists():
            return None
        data = json.loads(_UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
        _UPDATE_STATUS_FILE.unlink(missing_ok=True)
        tag = "OK" if data.get("ok") else "FAIL"
        return f"[previous background update {tag}] {data.get('message', '')}"
    except Exception:
        return None


def register(mcp, *, client, sync, hooks, config) -> None:
    """Register all MCP tools with explicit dependencies."""
    deps = ToolDependencies(
        client=client,
        config=config,
        sync=sync,
        hooks=hooks,
        read_update_status=_read_update_status,
        init_existing_project=partial(init_helpers._init_existing_project, client=client),
        init_match_or_create=partial(init_helpers._init_match_or_create, client=client),
        build_hooks_lines=partial(init_helpers._build_hooks_lines, hooks=hooks),
        local_impact=partial(impact.local_impact, client=client),
    )
    tools.register_all_tools(mcp, deps)
