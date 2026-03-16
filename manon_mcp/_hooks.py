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


def _install_codex_config() -> str | None:
    """Install Codex CLI MCP config + AGENTS.md rules. Returns status or None.

    Codex uses TOML config (~/.codex/config.toml) for MCP servers and
    AGENTS.md for behavioral rules (equivalent to Claude Code hooks).
    Idempotent: skips write if config already matches.
    """
    codex_dir = Path.home() / ".codex"
    config_file = codex_dir / "config.toml"
    agents_file = Path.home() / "AGENTS.md"

    if not codex_dir.exists():
        # Codex not installed
        return None

    try:
        # ── 1. MCP server in config.toml ──
        # We need to read existing TOML, inject [mcp_servers.manon] if missing
        venv_python = str(Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe")
        if not Path(venv_python).exists():
            # Unix path
            venv_python = str(Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python")
        venv_python = venv_python.replace("\\", "/")
        server_py = str(Path(__file__).resolve().parent.parent / "run_mcp.py").replace("\\", "/")

        api_key = _config.API_KEY or ""

        existing_toml = ""
        if config_file.exists():
            existing_toml = config_file.read_text(encoding="utf-8")

        # Check if manon MCP already configured
        if "[mcp_servers.manon]" in existing_toml:
            log.info("Codex MCP config already present, skipping")
        else:
            # Append MCP server config
            mcp_block = f'\n[mcp_servers.manon]\ncommand = "{venv_python}"\nargs = ["{server_py}"]\nenv = {{ MANON_API_KEY = "{api_key}" }}\nstartup_timeout_sec = 30.0\ntool_timeout_sec = 120.0\n'
            config_file.write_text(existing_toml.rstrip() + "\n" + mcp_block, encoding="utf-8")
            log.info("Codex MCP config installed: %s", config_file)

        # ── 2. AGENTS.md rules (equivalent to Claude Code hooks) ──
        agents_content = """# Codex AGENTS.md — Manon 知识图谱规则

## 核心规则（MUST）

代码理解、架构分析、代码搜索时，**必须优先使用 Manon MCP 工具**，禁止跳过图谱直接搜索文件。

## 工具选择

| 场景 | 工具 | 说明 |
|------|------|------|
| 代码搜索/理解 | `manon_search` | 语义搜索实体和关系 |
| 深度问答 | `manon_deep_query` | 多轮迭代，自动覆盖子问题 |
| 调用关系/依赖 | `manon_graph` | symbol 级图遍历 (callers/callees/both) |
| 改动影响 | `manon_impact` | commit 级影响传播分析 |
| 代码健康度 | `manon_code_health` | 8 维度评分 |
| 初始化/连接 | `manon_init` | 匹配或创建仓库 + 索引 |

## 强制规则（模拟 Claude Code Hooks）

### 规则 1：搜索前必查图谱

在使用 grep、find、文件搜索等操作前，**必须先用 manon_search / manon_deep_query / manon_graph 查询图谱**。
图谱不足时才用文件搜索补充，并声明"图谱未覆盖，补充搜索"。

### 规则 2：编辑代码前必查上下文

修改代码文件（.py/.js/.ts/.tsx/.jsx/.java/.go/.rs/.c/.cpp）前：
- 修改函数/类/import 等关键代码，或修改超过 10 行时，**必须先用 manon_search/manon_graph 了解上下文**
- 同时用 `git log --oneline -10 -- <file>` 查看近期改动，确认不会回退刚修过的设计决策
- 非代码文件（.json/.yaml/.md/.toml 等）不受此限制

### 规则 3：探索代码库前必查图谱

在进行大范围代码探索或规划前，**必须先用 manon_search / manon_deep_query 查询图谱**。
图谱不足时才进行文件级探索，并声明"图谱未覆盖，补充搜索"。

## 执行顺序

1. **先图谱，再补搜索**：不足时才用文件搜索，且声明"图谱未覆盖，补充搜索"
2. **改代码前必查**：先 `manon_search` 或 `manon_graph` 了解上下文
3. **查不到时**：`manon_repos_list` 返回空 → `manon_init`
4. **改前看近史**：修改前先 `git log --oneline -10 -- <file>` 或 `manon_impact` 查最近改动
"""
        # Check if AGENTS.md already has Manon rules
        existing_agents = ""
        if agents_file.exists():
            existing_agents = agents_file.read_text(encoding="utf-8")

        if "Manon" in existing_agents and "manon_search" in existing_agents:
            log.info("Codex AGENTS.md already has Manon rules, skipping")
        else:
            if existing_agents:
                # Append to existing
                agents_file.write_text(existing_agents.rstrip() + "\n\n" + agents_content, encoding="utf-8")
            else:
                agents_file.write_text(agents_content, encoding="utf-8")
            log.info("Codex AGENTS.md installed: %s", agents_file)

        # ── 3. Skill installation ──
        skills_dir = codex_dir / "skills" / "manon"
        skill_file = skills_dir / "SKILL.md"
        agents_yaml = skills_dir / "agents" / "openai.yaml"

        # Read SKILL.md from Claude Code skills as source of truth
        claude_skill_src = Path.home() / ".claude" / "skills" / "manon" / "SKILL.md"
        manon_root = Path(__file__).resolve().parent.parent

        if skill_file.exists() and "manon_init" in skill_file.read_text(encoding="utf-8"):
            log.info("Codex skill already installed, skipping")
        else:
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "agents").mkdir(parents=True, exist_ok=True)

            # Build SKILL.md content — use Claude Code skill if available, else embedded
            if claude_skill_src.exists():
                skill_content = claude_skill_src.read_text(encoding="utf-8")
                # Adapt frontmatter: remove user_invocable (Codex doesn't use it)
                skill_content = skill_content.replace("user_invocable: true\n", "")
                # Update description for Codex
                skill_content = skill_content.replace(
                    "description: /manon -- 进入 Manon 模式",
                    "description: /manon -- 进入 Manon 模式。代码理解、架构分析、代码搜索时，使用 Manon MCP 工具进行语义搜索、图遍历、影响分析。当用户输入 /manon 或需要初始化代码智能工具时触发。",
                )
            else:
                skill_content = """---
name: manon
description: /manon -- 进入 Manon 模式。代码理解、架构分析、代码搜索时，使用 Manon MCP 工具进行语义搜索、图遍历、影响分析。
---

# Manon -- 代码智能工具

调用 `manon_init` 初始化，然后按提示完成文件同步和索引。

| 场景 | 工具 |
|------|------|
| 代码理解/搜索 | `manon_deep_query` |
| 调用关系/依赖 | `manon_graph` |
| 改动影响 | `manon_impact` |
| 初始化 | `manon_init` |
"""
            skill_file.write_text(skill_content, encoding="utf-8")

            # Write agents/openai.yaml
            yaml_content = 'interface:\n  display_name: "Manon 代码智能"\n  short_description: "AI 架构师工具 — 语义搜索、图遍历、影响分析"\n  default_prompt: "/manon"\n'
            agents_yaml.write_text(yaml_content, encoding="utf-8")
            log.info("Codex skill installed: %s", skills_dir)

        return "🔗 Codex CLI 已配置（MCP + AGENTS.md + Skill）"
    except Exception as e:
        log.warning("Failed to install Codex config: %s", e)
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
