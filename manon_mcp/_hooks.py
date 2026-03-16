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


_PRE_EDIT_HOOK = '''\
"""PreToolUse hook: smart check before editing code."""
import json
import sys

data = json.load(sys.stdin)
tool_name = data.get("tool_name", "")
params = data.get("parameters", {})
fp = params.get("file_path", "")

non_code = (".json", ".yaml", ".yml", ".md", ".txt", ".xml", ".toml", ".ini", ".cfg")
if any(fp.endswith(ext) for ext in non_code):
    print(json.dumps({"continue": True}))
    sys.exit(0)

code_exts = (".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp")
if not any(fp.endswith(ext) for ext in code_exts):
    print(json.dumps({"continue": True}))
    sys.exit(0)

if tool_name == "Write":
    print(json.dumps({"continue": True, "message": "Use manon_search before creating related code when context is unclear."}))
    sys.exit(0)

if tool_name == "Edit":
    old = params.get("old_string", "")
    new = params.get("new_string", "")
    critical = ["def ", "class ", "interface ", "import ", "from ", "export ", "function ", "async def", "public ", "private "]
    has_critical = any(token in old or token in new for token in critical)
    lines = max(old.count("\\n") + 1, new.count("\\n") + 1)
    if has_critical or lines > 10:
        print(
            "Hook rule: inspect context with manon_search/manon_graph before large or structural edits.",
            file=sys.stderr,
        )
        sys.exit(2)
    print(json.dumps({"continue": True, "message": "Use manon_search first if more context is needed."}))
    sys.exit(0)

print(json.dumps({"continue": True}))
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


def _install_claude_hooks() -> str | None:
    """Install Claude Code PreToolUse hooks into ~/.claude/."""
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

        search_hook.write_text(_PRE_SEARCH_HOOK, encoding="utf-8")
        edit_hook.write_text(_PRE_EDIT_HOOK, encoding="utf-8")
        agent_hook.write_text(_PRE_AGENT_PLAN_HOOK, encoding="utf-8")

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
                settings = {}

        hooks_cfg = settings.setdefault("hooks", {})
        pre_tool = hooks_cfg.setdefault("PreToolUse", [])
        existing_manon = [
            entry
            for entry in pre_tool
            if "pre_search.py" in str(entry) or "pre_edit.py" in str(entry) or "pre_agent_plan.py" in str(entry)
        ]
        if existing_manon == desired_entries:
            log.info("Claude Code hooks already up-to-date, skipping write")
            return None

        pre_tool[:] = [
            entry
            for entry in pre_tool
            if "pre_search.py" not in str(entry)
            and "pre_edit.py" not in str(entry)
            and "pre_agent_plan.py" not in str(entry)
        ]
        pre_tool.extend(desired_entries)

        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.info("Claude Code hooks installed: %s", hooks_dir)
        return "Claude Code hooks installed"
    except Exception as exc:
        log.warning("Failed to install Claude Code hooks: %s", exc)
        return None


def _install_codex_config() -> str | None:
    """Install Codex CLI MCP config and Manon guidance."""
    codex_dir = Path.home() / ".codex"
    config_file = codex_dir / "config.toml"
    agents_file = Path.home() / "AGENTS.md"

    if not codex_dir.exists():
        return None

    try:
        venv_python = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
        if not venv_python.exists():
            venv_python = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
        server_py = Path(__file__).resolve().parent.parent / "run_mcp.py"
        api_key = _config.API_KEY or ""
        venv_python_str = str(venv_python).replace("\\", "/")
        server_py_str = str(server_py).replace("\\", "/")

        existing_toml = ""
        if config_file.exists():
            existing_toml = config_file.read_text(encoding="utf-8")

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

        agents_content = """# Codex AGENTS.md - Manon rules

## Core Rule

Use Manon MCP tools before repository-wide grep/glob exploration.

## Preferred Tools

- `manon_search` for semantic search
- `manon_deep_query` for iterative analysis
- `manon_graph` for dependency traversal
- `manon_impact` for change impact analysis
- `manon_init` for repository initialization
"""

        existing_agents = agents_file.read_text(encoding="utf-8") if agents_file.exists() else ""
        if "manon_search" not in existing_agents:
            if existing_agents:
                agents_file.write_text(existing_agents.rstrip() + "\n\n" + agents_content, encoding="utf-8")
            else:
                agents_file.write_text(agents_content, encoding="utf-8")
            log.info("Codex AGENTS.md installed: %s", agents_file)

        skills_dir = codex_dir / "skills" / "manon"
        skill_file = skills_dir / "SKILL.md"
        agents_yaml = skills_dir / "agents" / "openai.yaml"

        if not skill_file.exists() or "manon_init" not in skill_file.read_text(encoding="utf-8"):
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "agents").mkdir(parents=True, exist_ok=True)

            claude_skill_src = Path.home() / ".claude" / "skills" / "manon" / "SKILL.md"
            if claude_skill_src.exists():
                skill_content = claude_skill_src.read_text(encoding="utf-8").replace("user_invocable: true\n", "")
            else:
                skill_content = """---
name: manon
description: /manon -- enter Manon mode for semantic code search and graph analysis.
---

# Manon

Use `manon_init` first, then query the repository with `manon_search`, `manon_deep_query`, `manon_graph`, and `manon_impact`.
"""
            skill_file.write_text(skill_content, encoding="utf-8")
            agents_yaml.write_text(
                'interface:\n  display_name: "Manon"\n  short_description: "Semantic code search and graph analysis"\n  default_prompt: "/manon"\n',
                encoding="utf-8",
            )
            log.info("Codex skill installed: %s", skills_dir)

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
    manon_line = f'"{python_exe}" "{script_path}" "{resolved}"'
    manon_marker = "# Manon push hook"
    marker_line = f"{manon_marker} - knowledge graph update + health score"

    t2 = _time.time()
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
                log.debug("_install_hook: hook already exists, total %.2fs", _time.time() - t0)
                return None
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
            "\n".join(
                [
                    "#!/bin/sh",
                    marker_line,
                    manon_line,
                    "exit 0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
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
    return "Push hook installed"
