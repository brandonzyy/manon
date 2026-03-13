"""Manon MCP — git hook and Claude Code hook installation."""
from __future__ import annotations

import json
import logging

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
"""PreToolUse hook: enforce Manon-first before Grep/Glob."""
import json, sys
data = json.load(sys.stdin)
print(
    "Hook 规则：执行 Grep/Glob 前，必须先用 manon_search / manon_deep_query / manon_graph 查询图谱。"
    "图谱不足时才用 Grep/Glob 补充，并声明\\'图谱未覆盖，补充搜索\\'。",
    file=sys.stderr,
)
sys.exit(2)
'''

_PRE_EDIT_HOOK = '''\
"""PreToolUse hook: smart check before editing code."""
import json, sys
data = json.load(sys.stdin)
tool_name = data.get("tool_name", "")
params = data.get("parameters", {})
fp = params.get("file_path", "")

# Non-code files → pass
non_code = (".json",".yaml",".yml",".md",".txt",".xml",".toml",".ini",".cfg")
if any(fp.endswith(e) for e in non_code):
    print(json.dumps({"continue": True}))
    sys.exit(0)

# Code file check
code_exts = (".py",".js",".ts",".tsx",".jsx",".java",".go",".rs",".c",".cpp",".h",".hpp")
if not any(fp.endswith(e) for e in code_exts):
    print(json.dumps({"continue": True}))
    sys.exit(0)

# Write (new file) → light reminder
if tool_name == "Write":
    print(json.dumps({"continue": True, "message": "\\ud83d\\udca1 新建代码文件，建议先用 manon_search 了解相关模块。"}))
    sys.exit(0)

# Edit → analyze changes
if tool_name == "Edit":
    old = params.get("old_string", "")
    new = params.get("new_string", "")
    critical = ["def ","class ","interface ","import ","from ","export ","function ","async def","public ","private "]
    has_critical = any(k in old or k in new for k in critical)
    lines = max(old.count("\\\\n")+1, new.count("\\\\n")+1)
    if has_critical or lines > 10:
        msg = "Hook 规则：修改"
        if has_critical: msg += "函数/类/import等关键代码"
        if lines > 10: msg += f"大范围代码({lines}行)"
        msg += "前，必须先用 manon_search/manon_graph 了解上下文，并用 git log 查看近期改动。"
        print(msg, file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"continue": True, "message": "\\ud83d\\udca1 建议先用 manon_search 了解代码上下文。"}))
    sys.exit(0)

print(json.dumps({"continue": True}))
'''

_PRE_AGENT_PLAN_HOOK = '''\
"""PreToolUse hook: enforce Manon-first before Explore/general-purpose agents."""
import json, sys
data = json.load(sys.stdin)
tool_input = data.get("tool_input", {})
agent_type = tool_input.get("subagent_type", "")
if agent_type in ("Explore", "general-purpose"):
    print(
        "Hook 规则：spawn Explore/general-purpose agent 前，"
        "必须先用 manon_search / manon_deep_query 查询图谱。"
        "图谱不足时才用 Explore 补充，并声明\\'图谱未覆盖，补充搜索\\'。",
        file=sys.stderr,
    )
    sys.exit(2)
else:
    print(json.dumps({"continue": True}))
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
        agent_hook = hooks_dir / "pre_agent_plan.py"
        search_path = str(search_hook).replace("\\", "/")
        edit_path = str(edit_hook).replace("\\", "/")
        agent_path = str(agent_hook).replace("\\", "/")

        # Write hook scripts (these don't trigger Claude Code reload)
        search_hook.write_text(_PRE_SEARCH_HOOK, encoding="utf-8")
        edit_hook.write_text(_PRE_EDIT_HOOK, encoding="utf-8")
        agent_hook.write_text(_PRE_AGENT_PLAN_HOOK, encoding="utf-8")

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
            {
                "matcher": "Agent",
                "hooks": [{"type": "command", "command": f"python {agent_path}"}],
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
                          if "pre_search.py" in str(h) or "pre_edit.py" in str(h) or "pre_agent_plan.py" in str(h)]
        if existing_manon == desired_entries:
            log.info("Claude Code hooks already up-to-date, skipping write")
            return None

        pre_tool[:] = [h for h in pre_tool
                       if "pre_search.py" not in str(h) and "pre_edit.py" not in str(h) and "pre_agent_plan.py" not in str(h)]
        pre_tool.extend(desired_entries)

        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.info("Claude Code hooks installed: %s", hooks_dir)
        return "🔗 Claude Code hooks 已安装（3个：Grep/Glob强制查图谱, Edit/Write智能检查, Agent强制查图谱）"
    except Exception as e:
        log.warning("Failed to install Claude Code hooks: %s", e)
        return None


def _find_git_root(path: Path) -> Path | None:
    """Walk up from path to find the nearest .git directory. Pure Python, no subprocess."""
    p = path.resolve()
    while p != p.parent:
        if (p / ".git").is_dir():
            return p
        p = p.parent
    return None


def _install_hook(project_path: str) -> str | None:
    """Install pre-push hook if .git exists. Returns status message or None on skip/error."""
    import time as _time
    t0 = _time.time()

    resolved = Path(project_path).resolve()
    git_root = _find_git_root(resolved)
    log.debug("_install_hook: find git root took %.2fs", _time.time() - t0)
    if git_root is None:
        return None

    git_dir = git_root / ".git"
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
