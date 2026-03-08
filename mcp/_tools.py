"""Manon MCP — tool registration coordinator."""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

_tools_dir = _Path(__file__).parent / "tools"

# Load tools/ as a proper package so its internal relative imports work
def _load_tools_package():
    pkg_name = "manon_tools"
    spec = _ilu.spec_from_file_location(
        pkg_name,
        str(_tools_dir / "__init__.py"),
        submodule_search_locations=[str(_tools_dir)],
    )
    mod = _ilu.module_from_spec(spec)
    _sys.modules[pkg_name] = mod
    # Pre-register sub-modules so `from .X import ...` resolves
    for py in _tools_dir.glob("*.py"):
        if py.stem == "__init__":
            continue
        sub_name = f"{pkg_name}.{py.stem}"
        sub_spec = _ilu.spec_from_file_location(sub_name, str(py))
        sub_mod = _ilu.module_from_spec(sub_spec)
        _sys.modules[sub_name] = sub_mod
        sub_spec.loader.exec_module(sub_mod)
        setattr(mod, py.stem, sub_mod)
    spec.loader.exec_module(mod)
    return mod

tools = _load_tools_package()
impact = tools.impact
init_helpers = tools.init_helpers

log = logging.getLogger("manon-mcp")

# ── Injected dependencies ────────────────────────────
_client = None   # _client module
_sync = None     # _sync module
_hooks = None    # _hooks module
_config = None   # _config module


def init(client, sync, hooks, config, constants):
    """Inject dependencies from server.py."""
    global _client, _sync, _hooks, _config
    _client = client
    _sync = sync
    _hooks = hooks
    _config = config

    # Initialize sub-modules
    impact.init(client)
    init_helpers.init(client, hooks)

    tools.repo.init(client)
    tools.search.init(client, impact.local_impact)
    tools.index.init(client)
    tools.repo_crud.init(client, sync)
    tools.init.init(
        client, config, _read_update_status,
        init_helpers._init_existing_project,
        init_helpers._init_match_or_create,
        init_helpers._build_hooks_lines,
    )
    tools.config.init(client, config)
    tools.query.init(client)
    tools.utility.init(client, config, _read_update_status)
    tools.health.init(client, hooks)
    tools.dynamic.init(client)


# ── Update helpers ───────────────────────────────────
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
        tag = "✓" if data.get("ok") else "✗"
        return f"[上次后台更新 {tag}] {data.get('message', '')}"
    except Exception:
        return None


# ── Tool registration ────────────────────────────────

def register(mcp):
    """Register all MCP tools on the given FastMCP instance."""
    tools.register_all_tools(mcp)
