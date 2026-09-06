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


_MANON_SCOPE = '''\
"""会话钩子的作用面 —— 当前目录属于哪个 manon 已注册的仓。

Manon-first 那几个钩子原本对**任何仓**生效，而本机 12 个 git 仓里只有 7 个注册过。
后果分两头：pre_search 在没有索引的仓里也拦住 Grep/Glob，那里根本没有 manon_search
可退回；post_commit 在每个仓提交后都注入一句 "You MUST run manon_impact"，
那条命令在没注册的仓里跑不出结果——**在做不到的地方下 MUST，磨损的是所有 MUST 的分量。**

注册表就用 manon 自己写的 ~/.manon/projects.json，不另立一份名单：
两份名单迟早不一致，而不一致的表现是钩子在已经注册了的仓里不说话。

本文件由 manon_mcp/_hooks.py 生成，手改会在下次 MCP init 时被覆盖。
"""
import json
import os
import time
from pathlib import Path

REGISTRY = ".manon/projects.json"
QUERY_STATE = ".manon/last_query.json"
QUERY_WINDOW = 3600  # 秒——「查过 manon」的时效，超窗就当没查过，提示重查一次。


def registry(home=None):
    """<仓的绝对路径> -> repo_id。读不到就返回空表——空表等于所有仓都不在作用面内，
    钩子于是全部放行。这个方向是刻意的：注册表读不到时宁可不说话，
    也不要在一个查不到索引的仓里拦住工具。"""
    q = (Path.home() if home is None else home) / REGISTRY
    try:
        data = json.loads(q.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    out = {}
    for path, meta in (data.get("projects") or {}).items():
        rid = (meta or {}).get("repo_id") if isinstance(meta, dict) else None
        if rid:
            out[Path(path)] = str(rid)
    return out


def repo_of(cwd=None, home=None):
    """cwd 落在哪个已注册的仓里，取最长匹配（仓套仓时内层说了算）。"""
    try:
        here = Path(cwd or os.getcwd()).resolve()
    except OSError:
        return None
    best = None
    for root, rid in registry(home).items():
        try:
            r = root.resolve()
        except OSError:
            continue
        if r == here or r in here.parents:
            if best is None or len(str(r)) > len(str(best[0])):
                best = (r, rid)
    return best


def manon_queried(root, home=None, now=None):
    """root 这个仓在 QUERY_WINDOW 内查过 manon 没有。

    时间戳由 MCP 服务端在处理查询时写（manon_mcp/query_state.py），这里只读。
    状态**不可知**——文件不存在（冷启动：还没装过会写它的服务端版本）、读不了、
    不是合法 JSON——按查过处理：同 registry() 的方向，这条规则是提醒不是
    安全边界，宁可漏拦一次提醒，也不要回到那道永远过不去的门。
    「文件在而该仓没有条目」不算不可知：那是明确没查过，照拦。"""
    q = (Path.home() if home is None else home) / QUERY_STATE
    try:
        state = json.loads(q.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return True
    if not isinstance(state, dict):
        return True  # 文件在但结构不对——写入方坏了，同「读不到」方向处理
    try:
        ts = float(state[str(root)])
    except (KeyError, TypeError, ValueError):
        return False  # 文件是好的，这个仓没有有效条目——明确没查过
    if now is None:
        now = time.time()
    return (now - ts) <= QUERY_WINDOW
'''


_PRE_SEARCH_HOOK = '''\
"""PreToolUse hook: Grep/Glob 之前先走 Manon —— 只在 manon 已注册的仓里。

作用面（2026-08-27 收窄）：原先对所有仓无条件退 2，包括根本没有索引的仓，
那里没有 manon_search 可退回，拦下来只剩「拦」。

它拦不住 Bash(grep)：钩子按工具名匹配，而 Bash 是另一个工具。
所以这是一条提示级约束，不是强制——写在这里省得下一个人把它当成强制。

本文件由 manon_mcp/_hooks.py 生成，手改会在下次 MCP init 时被覆盖。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manon_scope import repo_of  # noqa: E402

try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    sys.exit(0)

hit = repo_of(data.get("cwd"))
if hit is None:
    sys.exit(0)

print("Hook rule: call manon_search, manon_deep_query, or manon_graph before "
      "Grep/Glob (repo_id=%s)." % hit[1], file=sys.stderr)
sys.exit(2)
'''



_POST_COMMIT_HOOK = '''\
"""PostToolUse hook: 提交成功后提醒跑 manon_impact —— 只在 manon 已注册的仓里。

两处收窄（2026-08-27）：

1. 作用面。原先在任何仓提交后都注入这段话，而 manon_impact 需要 repo_id，
   本机 12 个仓里只有 7 个注册过。在跑不出结果的仓里下一句 MUST，
   得到的不是执行，是对 MUST 这个词的贬值——下一条真需要 MUST 的指令跟着一起贬。
2. 措辞。原先写 "CRITICAL ... You MUST immediately ... Do NOT proceed with other
   tasks until"。影响分析是有用的下一步，不是不做就出事的那一档；把每件事都写成
   最高级，等于没有等级。repo_id 现在从注册表取真值，不再让读的人自己去填。

本文件由 manon_mcp/_hooks.py 生成，手改会在下次 MCP init 时被覆盖。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manon_scope import repo_of  # noqa: E402

try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    sys.exit(0)

if data.get("tool_name") != "Bash":
    sys.exit(0)

command = (data.get("tool_input") or {}).get("command", "")
if (data.get("tool_response") or {}).get("exitCode", -1) != 0:
    sys.exit(0)

if "git commit" not in command:
    sys.exit(0)

hit = repo_of(data.get("cwd"))
if hit is None:
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "提交成功。下一步跑 manon_impact(repo_id=%r) 看这次改动波及哪些"
            "下游调用方，再决定要不要跟着改。" % hit[1]
        ),
    }
}, ensure_ascii=False))
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
"""PreToolUse hook: 起 Explore/general-purpose 子代理之前先查 Manon —— 只在已注册的仓里。

作用面同 pre_search.py：没有索引的仓里，「先查 manon」没有可查的东西。

拦截是有时效的（2026-09-07）：本会话在 QUERY_WINDOW（60 分钟）内查过这个仓的
manon（search / deep_query / graph / code_health 等都算）就放行。原先无条件退 2，
又不记录「查过没有」，按字面永远过不去——模型于是学会省略 subagent_type 绕行，
而省略类型会让客户端对内置子代理的模型覆盖（builtInModelOverrides）不生效，
子代理全跑在主模型上。**一道永远过不去的门，训练出来的不是「先查」，是「绕门」。**

时间戳由 MCP 服务端写 ~/.manon/last_query.json（manon_mcp/query_state.py），
这里经 manon_scope.manon_queried 只读；读不到按查过处理（fail-open）。

本文件由 manon_mcp/_hooks.py 生成，手改会在下次 MCP init 时被覆盖。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manon_scope import manon_queried, repo_of  # noqa: E402

try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    sys.exit(0)

agent_type = (data.get("tool_input") or {}).get("subagent_type", "")
hit = repo_of(data.get("cwd"))
if (agent_type in ("Explore", "general-purpose") and hit is not None
        and not manon_queried(hit[0])):
    print("Hook rule: query Manon (manon_search / manon_deep_query / manon_graph / "
          "manon_code_health, repo_id=%s) before spawning Explore/general-purpose "
          "agents for repository exploration." % hit[1], file=sys.stderr)
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
    # Use the venv python so hooks work even when "python" is not on PATH
    venv_python = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
    if not venv_python.exists():
        # Windows fallback
        venv_python = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
    py = str(venv_python).replace("\\", "/") if venv_python.exists() else "python3"

    desired_pre = [
        {"matcher": "Grep|Glob",      "hooks": [{"type": "command", "command": f"{py} {search_path}"}]},
        {"matcher": "Agent",          "hooks": [{"type": "command", "command": f"{py} {agent_path}"}]},
        {"matcher": "EnterPlanMode",  "hooks": [{"type": "command", "command": f"{py} {enter_plan_path}"}]},
    ]
    desired_post = [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": f"{py} {commit_path}"}]},
    ]
    desired_stop = [
        {"hooks": [{"type": "command", "command": f"{py} {stop_dao_path}"}]},
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
        scope_mod        = hooks_dir / "manon_scope.py"

        search_hook.write_text(_PRE_SEARCH_HOOK,      encoding="utf-8")
        agent_hook.write_text(_PRE_AGENT_PLAN_HOOK,   encoding="utf-8")
        enter_plan_hook.write_text(_PRE_ENTER_PLAN_HOOK, encoding="utf-8")
        commit_hook.write_text(_POST_COMMIT_HOOK,     encoding="utf-8")
        stop_dao_hook.write_text(_STOP_DAO_HOOK,      encoding="utf-8")
        # 三个 Manon-first 钩子共用的作用面判定。**必须跟着一起装**：
        # 少了它，那三个钩子 import 就炸，而钩子炸掉的表现是安静地放行。
        scope_mod.write_text(_MANON_SCOPE,            encoding="utf-8")

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


