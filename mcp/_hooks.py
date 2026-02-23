"""Manon MCP — git hook and Claude Code hook installation."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("manon-mcp")

# ── Injected dependencies ────────────────────────────
_config = None  # _config module


def init(config):
    """Inject dependencies from server.py."""
    global _config
    _config = config


# ── Claude Code hook scripts ─────────────────────────

_PRE_SEARCH_HOOK = '''\
"""PreToolUse hook: remind to use Manon graph before Grep/Glob."""
import json, sys
data = json.load(sys.stdin)
print(json.dumps({
    "continue": True,
    "message": (
        "\\u26a0\\ufe0f 规则提醒：是否已先用 manon_search / manon_graph 查过图谱？"
        "如未查，请先查图谱再补搜索，并声明\\'图谱未覆盖，补充搜索\\'。"
    ),
}))
'''

_PRE_EDIT_HOOK = '''\
"""PreToolUse hook: remind to check context before editing code."""
import json, sys
data = json.load(sys.stdin)
fp = data.get("parameters", {}).get("file_path", "")
exts = (".py",".js",".ts",".tsx",".jsx",".java",".go",".rs",".c",".cpp",".h")
if not any(fp.endswith(e) for e in exts):
    print(json.dumps({"continue": True}))
    sys.exit(0)
print(json.dumps({
    "continue": True,
    "message": (
        "\\u26a0\\ufe0f 改代码前必查：1) manon_search/manon_graph 了解上下文 "
        "2) git log --oneline -10 -- <file> 看近期改动，避免回退已有设计决策。"
    ),
}))
'''


def _persist_api_config() -> None:
    """Save current API_URL and API_KEY to ~/.manon/config.json."""
    cfg_file = Path.home() / ".manon" / "config.json"
    try:
        existing = {}
        if cfg_file.exists():
            existing = json.loads(cfg_file.read_text(encoding="utf-8"))
        existing["api_url"] = _config.API_URL
        if _config.API_KEY:
            existing["api_key"] = _config.API_KEY
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to persist API config: %s", e)


def _install_claude_hooks() -> str | None:
    """Install Claude Code PreToolUse hooks into ~/.claude/. Returns status or None.

    Idempotent: skips settings.json write if hooks already match,
    avoiding Claude Code config-reload which can disrupt MCP connections.
    """
    claude_dir = Path.home() / ".claude"
    hooks_dir = claude_dir / "hooks"
    settings_file = claude_dir / "settings.json"

    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        search_hook = hooks_dir / "pre_search.py"
        edit_hook = hooks_dir / "pre_edit.py"
        search_path = str(search_hook).replace("\\", "/")
        edit_path = str(edit_hook).replace("\\", "/")

        # Write hook scripts (these don't trigger Claude Code reload)
        search_hook.write_text(_PRE_SEARCH_HOOK, encoding="utf-8")
        edit_hook.write_text(_PRE_EDIT_HOOK, encoding="utf-8")

        # Build desired hooks entries
        desired_entries = [
            {
                "matcher": "Grep|Glob",
                "hooks": [{"type": "command", "command": f"python {search_path}"}],
            },
            {
                "matcher": "Edit|Write",
                "hooks": [{"type": "command", "command": f"python {edit_path}"}],
            },
        ]

        settings: dict = {}
        if settings_file.exists():
            try:
                settings = json.loads(settings_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        hooks_cfg = settings.setdefault("hooks", {})
        pre_tool = hooks_cfg.setdefault("PreToolUse", [])

        # Check if hooks already match — skip write to avoid triggering
        # Claude Code config reload which disrupts the MCP connection.
        existing_manon = [h for h in pre_tool
                          if "pre_search.py" in str(h) or "pre_edit.py" in str(h)]
        if existing_manon == desired_entries:
            log.info("Claude Code hooks already up-to-date, skipping write")
            return None

        pre_tool[:] = [h for h in pre_tool
                       if "pre_search.py" not in str(h) and "pre_edit.py" not in str(h)]
        pre_tool.extend(desired_entries)

        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.info("Claude Code hooks installed: %s", hooks_dir)
        return "🔗 Claude Code hooks 已安装（Grep/Glob → 先查图谱, Edit/Write → 查上下文）"
    except Exception as e:
        log.warning("Failed to install Claude Code hooks: %s", e)
        return None


def _install_hook(project_path: str) -> str | None:
    """Install pre-push hook if .git exists. Returns status message or None."""
    import time as _time
    t0 = _time.time()

    resolved = Path(project_path).resolve()
    log.debug("_install_hook: resolve path took %.2fs", _time.time() - t0)

    t1 = _time.time()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(resolved), capture_output=True, text=True, encoding="utf-8",
            stdin=subprocess.DEVNULL, timeout=5,
        )
        log.debug("_install_hook: git rev-parse took %.2fs", _time.time() - t1)
        if result.returncode == 0 and result.stdout.strip():
            git_root = Path(result.stdout.strip()).resolve()
        else:
            return None
    except Exception as e:
        log.debug("_install_hook: git rev-parse failed after %.2fs: %s", _time.time() - t1, e)
        return None

    git_dir = git_root / ".git"
    if not git_dir.is_dir():
        return None
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_file = hooks_dir / "pre-push"
    script_path = Path(__file__).resolve().parent / "hooks" / "post_push.py"
    python_exe = sys.executable or "python3"
    manon_line = f'"{python_exe}" "{script_path}" "{resolved}"'
    manon_marker = "# Manon push hook"

    t2 = _time.time()
    if hook_file.exists():
        existing = hook_file.read_text(encoding="utf-8", errors="replace")
        if manon_marker in existing:
            log.debug("_install_hook: hook already exists, total %.2fs", _time.time() - t0)
            return None
        lines = existing.rstrip().split("\n")
        insert_idx = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "exit 0":
                insert_idx = i
                break
        lines.insert(insert_idx, f"\n{manon_marker} — knowledge graph update + health score")
        lines.insert(insert_idx + 1, manon_line)
        hook_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        hook_content = f"""#!/bin/sh
{manon_marker} — knowledge graph update + health score
{manon_line}
exit 0
"""
        hook_file.write_text(hook_content, encoding="utf-8")
    log.debug("_install_hook: write hook took %.2fs", _time.time() - t2)

    t3 = _time.time()
    try:
        hook_file.chmod(0o755)
    except Exception:
        pass
    log.debug("_install_hook: chmod took %.2fs", _time.time() - t3)

    t4 = _time.time()
    _persist_api_config()
    log.debug("_install_hook: persist config took %.2fs", _time.time() - t4)

    log.info("_install_hook: total %.2fs", _time.time() - t0)
    return "🔗 Push hook 已安装"
