"""Manon MCP — tool registration coordinator."""
from __future__ import annotations

import datetime
import json
import logging
import subprocess
import sys
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
INLINE_SCAN_LIMIT = 50


def init(client, sync, hooks, config, constants):
    """Inject dependencies from server.py."""
    global _client, _sync, _hooks, _config, INLINE_SCAN_LIMIT
    _client = client
    _sync = sync
    _hooks = hooks
    _config = config
    INLINE_SCAN_LIMIT = constants["INLINE_SCAN_LIMIT"]

    # Initialize sub-modules
    impact.init(client)
    init_helpers.init(client, sync, hooks)

    tools.repo.init(client)
    tools.search.init(client, impact.local_impact)
    tools.index.init(client, sync)
    tools.repo_crud.init(client, sync)
    tools.init.init(
        client, config, _read_update_status,
        init_helpers._init_existing_project,
        init_helpers._init_match_or_create,
        init_helpers._build_hooks_lines,
    )
    tools.config.init(client, config)
    tools.query.init(client)
    tools.utility.init(client, config, _read_update_status, _do_update)
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


def _do_update() -> list[str]:
    """Execute git pull + pip install. Writes result to status file."""
    install_dir = Path(__file__).resolve().parent.parent
    lines: list[str] = []
    ok = False
    branch = _config._git_branch()

    try:
        result = subprocess.run(
            ["git", "pull", "--quiet", "origin", branch],
            cwd=str(install_dir),
            capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=15,
        )
        git_out = result.stdout.strip()
        if "Already up to date" in git_out or "Already up-to-date" in git_out or not git_out:
            lines.append("代码已是最新，无需更新。")
            ok = True
            _write_update_status(ok, lines)
            return lines
        lines.append(f"代码已更新:\n{git_out}")
    except subprocess.TimeoutExpired:
        lines.append("git pull 超时（15s），请手动执行: cd manon && git pull")
        _write_update_status(False, lines)
        return lines
    except Exception as e:
        lines.append(f"git pull 失败: {e}")
        _write_update_status(False, lines)
        return lines

    req_file = install_dir / "mcp" / "requirements.txt"
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
        )
        lines.append("依赖已更新。")
        ok = True
    except subprocess.TimeoutExpired:
        lines.append("pip install 超时，请手动执行: pip install -r mcp/requirements.txt")
    except Exception as e:
        lines.append(f"依赖安装失败: {e}")

    lines.append("请重启 Claude Code 使新版本生效。")
    _write_update_status(ok, lines)
    return lines


# ── Tool registration ────────────────────────────────

def register(mcp):
    """Register all MCP tools on the given FastMCP instance."""
    tools.register_all_tools(mcp)
