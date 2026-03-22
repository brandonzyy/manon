"""Manon MCP hook and client-config installation helpers."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("manon-mcp")

_config = None


def init(config):
    """Inject config dependencies from server startup."""
    global _config
    _config = config


_PRE_ENTER_PLAN_HOOK = '''\
"""PreToolUse hook: auto-write dao marker when EnterPlanMode plan contains DAO: header."""
import json
import re
import sys
from pathlib import Path

DAO_MARKER = Path.home() / ".dao_plan_active"

data = json.load(sys.stdin)
plan = str(data.get("tool_input", {}).get("plan", "") or "")

# Expect first line: DAO: project=<path> issue=<id> skill=<dir> repo=<id>
m = re.search(
    r"DAO:\s+project=(.+?)\s+issue=(\S+)\s+skill=(.+?)\s+repo=(\S+)",
    plan,
)
if m:
    project_path, issue_id, skill_dir, repo_id = m.group(1), m.group(2), m.group(3), m.group(4)
    DAO_MARKER.write_text(
        f"{project_path}|||{issue_id}|||{skill_dir}|||{repo_id}",
        encoding="utf-8",
    )

sys.exit(0)
'''


_PRE_SEARCH_HOOK = '''\
"""PreToolUse hook: enforce Manon-first before Grep/Glob."""
import json
import sys

json.load(sys.stdin)
print(
    "Hook rule: call manon_search, manon_deep_query, or manon_graph before Grep/Glob.",
    file=sys.stderr,
)
sys.exit(2)
'''



_POST_COMMIT_HOOK = '''\
"""PostToolUse hook: manon_impact reminder after successful git commit."""
import json
import sys

data = json.load(sys.stdin)
if data.get("tool_name") != "Bash":
    sys.exit(0)

command = data.get("tool_input", {}).get("command", "")
if data.get("tool_response", {}).get("exitCode", -1) != 0:
    sys.exit(0)

if not any(t in command for t in ("git commit", "git commit -m", "git commit -am")):
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "CRITICAL: Git commit succeeded. You MUST immediately run "
            "manon_impact(<repo_id>) to analyze this commit\'s impact on "
            "dependencies and downstream callers. Do NOT proceed with other "
            "tasks until impact analysis is complete."
        ),
    }
}))
'''


_STOP_DAO_HOOK = '''\
"""Stop hook: block Claude from stopping if dao session has pending commit."""
import json
import os
import sys
from pathlib import Path

DAO_MARKER = Path.home() / ".dao_plan_active"

try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    sys.exit(0)

# Prevent infinite loop: if Stop hook already fired once this turn, let Claude stop
if data.get("stop_hook_active"):
    sys.exit(0)

if not DAO_MARKER.exists():
    sys.exit(0)

try:
    parts = DAO_MARKER.read_text(encoding="utf-8").strip().split("|||")
    project_path, issue_id, skill_dir, repo_id = parts[0], parts[1], parts[2], parts[3]
except Exception:
    DAO_MARKER.unlink(missing_ok=True)
    sys.exit(0)

# Only block if current working directory is inside the dao session\'s project
try:
    cwd = Path(os.getcwd()).resolve()
    target = Path(project_path).resolve()
    if cwd != target and target not in cwd.parents and cwd not in target.parents:
        sys.exit(0)
except Exception:
    sys.exit(0)

print(json.dumps({
    "decision": "block",
    "reason": (
        f"DAO SESSION INCOMPLETE. Run this command now before stopping:\\n"
        f"MANON_DAO_MSG=\\"<commit message>\\" "
        f"python \\"{skill_dir}/scripts/dao-commit.py\\" "
        f"\\"{project_path}\\" \\"{issue_id}\\" \\"{skill_dir}\\" \\"{repo_id}\\"\\n"
        f"Then: manon_impact(repo_id=\'{repo_id}\', commit=\'HEAD\') and sync graph."
    ),
}))
'''


_PRE_AGENT_PLAN_HOOK = '''\
"""PreToolUse hook: enforce Manon-first before Explore/general-purpose agents."""
import json
import sys

data = json.load(sys.stdin)
tool_input = data.get("tool_input", {})
agent_type = tool_input.get("subagent_type", "")
if agent_type in ("Explore", "general-purpose"):
    print(
        "Hook rule: query Manon before spawning Explore/general-purpose agents for repository exploration.",
        file=sys.stderr,
    )
    sys.exit(2)

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
    except Exception as exc:
        log.warning("Failed to persist API config: %s", exc)


def _build_claude_hook_entries(
    search_path: str, agent_path: str, commit_path: str,
    enter_plan_path: str, stop_dao_path: str,
) -> tuple[list, list, list]:
    desired_pre = [
        {"matcher": "Grep|Glob",      "hooks": [{"type": "command", "command": f"python {search_path}"}]},
        {"matcher": "Agent",          "hooks": [{"type": "command", "command": f"python {agent_path}"}]},
        {"matcher": "EnterPlanMode",  "hooks": [{"type": "command", "command": f"python {enter_plan_path}"}]},
    ]
    desired_post = [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": f"python {commit_path}"}]},
    ]
    desired_stop = [
        {"hooks": [{"type": "command", "command": f"python {stop_dao_path}"}]},
    ]
    return desired_pre, desired_post, desired_stop


def _update_settings_hooks(
    settings_file: Path, desired_pre: list, desired_post: list, desired_stop: list,
) -> bool:
    _manon_hook_files = (
        "pre_search.py", "pre_edit.py", "pre_agent_plan.py",
        "pre_enter_plan.py", "post_commit.py", "stop_dao.py",
    )
    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except Exception:
            settings = {}

    hooks_cfg = settings.setdefault("hooks", {})
    pre_tool   = hooks_cfg.setdefault("PreToolUse", [])
    post_tool  = hooks_cfg.setdefault("PostToolUse", [])
    stop_hooks = hooks_cfg.setdefault("Stop", [])

    # Clean up retired hook files from settings
    _retired = ("post_exit_plan.py",)
    for section in (pre_tool, post_tool, stop_hooks):
        section[:] = [e for e in section if not any(r in str(e) for r in _retired)]

    existing_pre  = [e for e in pre_tool   if any(f in str(e) for f in _manon_hook_files)]
    existing_post = [e for e in post_tool  if any(f in str(e) for f in _manon_hook_files)]
    existing_stop = [e for e in stop_hooks if any(f in str(e) for f in _manon_hook_files)]
    if existing_pre == desired_pre and existing_post == desired_post and existing_stop == desired_stop:
        return False

    pre_tool[:]   = [e for e in pre_tool   if not any(f in str(e) for f in _manon_hook_files)]
    post_tool[:]  = [e for e in post_tool  if not any(f in str(e) for f in _manon_hook_files)]
    stop_hooks[:] = [e for e in stop_hooks if not any(f in str(e) for f in _manon_hook_files)]
    pre_tool.extend(desired_pre)
    post_tool.extend(desired_post)
    stop_hooks.extend(desired_stop)
    settings_file.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def _install_claude_hooks() -> str | None:
    """Install Claude Code hooks into ~/.claude/."""
    claude_dir = Path.home() / ".claude"
    hooks_dir  = claude_dir / "hooks"
    settings_file = claude_dir / "settings.json"

    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)

        search_hook      = hooks_dir / "pre_search.py"
        agent_hook       = hooks_dir / "pre_agent_plan.py"
        enter_plan_hook  = hooks_dir / "pre_enter_plan.py"
        commit_hook      = hooks_dir / "post_commit.py"
        stop_dao_hook    = hooks_dir / "stop_dao.py"

        search_hook.write_text(_PRE_SEARCH_HOOK,      encoding="utf-8")
        agent_hook.write_text(_PRE_AGENT_PLAN_HOOK,   encoding="utf-8")
        enter_plan_hook.write_text(_PRE_ENTER_PLAN_HOOK, encoding="utf-8")
        commit_hook.write_text(_POST_COMMIT_HOOK,     encoding="utf-8")
        stop_dao_hook.write_text(_STOP_DAO_HOOK,      encoding="utf-8")

        # Remove retired hook files
        for retired in ("pre_edit.py", "post_exit_plan.py"):
            p = hooks_dir / retired
            if p.exists():
                p.unlink()

        s = str(search_hook).replace("\\", "/")
        a = str(agent_hook).replace("\\", "/")
        e = str(enter_plan_hook).replace("\\", "/")
        c = str(commit_hook).replace("\\", "/")
        d = str(stop_dao_hook).replace("\\", "/")

        desired_pre, desired_post, desired_stop = _build_claude_hook_entries(s, a, c, e, d)
        if not _update_settings_hooks(settings_file, desired_pre, desired_post, desired_stop):
            log.info("Claude Code hooks already up-to-date, skipping write")
            return None

        log.info("Claude Code hooks installed: %s", hooks_dir)
        return "Claude Code hooks installed"
    except Exception as exc:
        log.warning("Failed to install Claude Code hooks: %s", exc)
        return None


_CODEX_AGENTS_CONTENT = """# Codex AGENTS.md - Manon rules

## Core Rule

Use Manon MCP tools before repository-wide grep/glob exploration.

## Preferred Tools

- `manon_search` for semantic search
- `manon_deep_query` for iterative analysis
- `manon_graph` for dependency traversal
- `manon_impact` for change impact analysis
- `manon_init` for repository initialization
"""


def _install_codex_mcp_block(config_file: Path, venv_python_str: str, server_py_str: str, api_key: str) -> None:
    """Append Manon MCP server block to Codex config.toml if not present."""
    existing_toml = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
    if "[mcp_servers.manon]" not in existing_toml:
        mcp_block = (
            '\n[mcp_servers.manon]\n'
            f'command = "{venv_python_str}"\n'
            f'args = ["{server_py_str}"]\n'
            f'env = {{ MANON_API_KEY = "{api_key}" }}\n'
            "startup_timeout_sec = 30.0\n"
            "tool_timeout_sec = 120.0\n"
        )
        config_file.write_text(existing_toml.rstrip() + "\n" + mcp_block, encoding="utf-8")
        log.info("Codex MCP config installed: %s", config_file)


def _install_codex_agents_md(agents_file: Path) -> None:
    """Append Manon guidance to ~/AGENTS.md if not present."""
    existing = agents_file.read_text(encoding="utf-8") if agents_file.exists() else ""
    if "manon_search" not in existing:
        agents_file.write_text((existing.rstrip() + "\n\n" + _CODEX_AGENTS_CONTENT) if existing else _CODEX_AGENTS_CONTENT, encoding="utf-8")
        log.info("Codex AGENTS.md installed: %s", agents_file)


def _install_codex_skill(codex_dir: Path) -> None:
    """Install Manon skill files into ~/.codex/skills/manon/."""
    skills_dir = codex_dir / "skills" / "manon"
    skill_file = skills_dir / "SKILL.md"
    agents_yaml = skills_dir / "agents" / "openai.yaml"
    if skill_file.exists() and "manon_init" in skill_file.read_text(encoding="utf-8"):
        return
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "agents").mkdir(parents=True, exist_ok=True)
    claude_skill_src = Path.home() / ".claude" / "skills" / "manon" / "SKILL.md"
    if claude_skill_src.exists():
        skill_content = claude_skill_src.read_text(encoding="utf-8").replace("user_invocable: true\n", "")
    else:
        skill_content = "---\nname: manon\ndescription: /manon -- enter Manon mode for semantic code search and graph analysis.\n---\n\n# Manon\n\nUse `manon_init` first, then query the repository with `manon_search`, `manon_deep_query`, `manon_graph`, and `manon_impact`.\n"
    skill_file.write_text(skill_content, encoding="utf-8")
    agents_yaml.write_text('interface:\n  display_name: "Manon"\n  short_description: "Semantic code search and graph analysis"\n  default_prompt: "/manon"\n', encoding="utf-8")
    log.info("Codex skill installed: %s", skills_dir)


def _install_codex_config() -> str | None:
    """Install Codex CLI MCP config and Manon guidance."""
    codex_dir = Path.home() / ".codex"
    if not codex_dir.exists():
        return None

    try:
        venv_python = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
        if not venv_python.exists():
            venv_python = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
        server_py = Path(__file__).resolve().parent.parent / "run_mcp.py"
        venv_python_str = str(venv_python).replace("\\", "/")
        server_py_str = str(server_py).replace("\\", "/")

        _install_codex_mcp_block(codex_dir / "config.toml", venv_python_str, server_py_str, _config.API_KEY or "")
        _install_codex_agents_md(Path.home() / "AGENTS.md")
        _install_codex_skill(codex_dir)
        return "Codex CLI configured"
    except Exception as exc:
        log.warning("Failed to install Codex config: %s", exc)
        return None


def _find_git_root(path: Path) -> Path | None:
    """Walk up from path to find the nearest .git directory."""
    current = path.resolve()
    while current != current.parent:
        if (current / ".git").is_dir():
            return current
        current = current.parent
    return None


def _write_hook_file(hook_file: Path, manon_marker: str, marker_line: str, manon_line: str) -> bool:
    """Update or create hook_file. Returns False if no change needed, True if written."""
    if hook_file.exists():
        lines = hook_file.read_text(encoding="utf-8", errors="replace").rstrip().split("\n")
        marker_idx = next((i for i, line in enumerate(lines) if manon_marker in line), -1)
        if marker_idx >= 0:
            changed = False
            if lines[marker_idx] != marker_line:
                lines[marker_idx] = marker_line
                changed = True
            if marker_idx + 1 < len(lines):
                if lines[marker_idx + 1] != manon_line:
                    lines[marker_idx + 1] = manon_line
                    changed = True
            else:
                lines.append(manon_line)
                changed = True
            if not changed:
                return False
        else:
            insert_idx = len(lines)
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip() == "exit 0":
                    insert_idx = i
                    break
            lines.insert(insert_idx, marker_line)
            lines.insert(insert_idx + 1, manon_line)
        hook_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        hook_file.write_text(
            "\n".join(["#!/bin/sh", marker_line, manon_line, "exit 0"]) + "\n",
            encoding="utf-8",
        )
    return True


def _install_hook(project_path: str) -> str | None:
    """Install or upgrade the git pre-push hook for Manon sync."""
    import time as _time
    t0 = _time.time()
    resolved = Path(project_path).resolve()
    git_root = _find_git_root(resolved)
    log.debug("_install_hook: find git root took %.2fs", _time.time() - t0)
    if git_root is None:
        return None

    hooks_dir = git_root / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_file = hooks_dir / "pre-push"
    script_path = Path(__file__).resolve().parent / "hooks" / "post_push.py"
    python_exe = sys.executable or "python3"
    manon_marker = "# Manon push hook"
    marker_line = f"{manon_marker} - knowledge graph update + health score"
    manon_line = f'nohup "{python_exe}" "{script_path}" "{resolved}" >/dev/null 2>&1 &'

    t2 = _time.time()
    written = _write_hook_file(hook_file, manon_marker, marker_line, manon_line)
    log.debug("_install_hook: write hook took %.2fs", _time.time() - t2)
    if not written:
        log.debug("_install_hook: hook already exists, total %.2fs", _time.time() - t0)
        return None

    try:
        hook_file.chmod(0o755)
    except Exception:
        pass
    _persist_api_config()
    log.info("_install_hook: total %.2fs", _time.time() - t0)
    return "Push hook installed"


