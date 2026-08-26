#!/usr/bin/env bash
set -euo pipefail

# ── Manon MCP Installer ──────────────────────────────
# Multi-platform installer for Manon code intelligence:
#   1. Python venv + dependencies
#   2. MCP server registration (Claude Code / Cursor / Windsurf / OpenCode)
#   3. Deep-query behavior rules for each platform
# ─────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SERVER_PY="$SCRIPT_DIR/run_mcp.py"
LAUNCHER="$SCRIPT_DIR/launch_mcp.sh"
VENV_DIR="$SCRIPT_DIR/.venv"
API_URL_CN="http://saas.matrixone.online:3700"

# ── Colors ────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[x]${NC} $1"; exit 1; }
head1() { echo -e "\n${CYAN}── $1 ──${NC}"; }

# ── Shared content ────────────────────────────────────
MANON_RULES='# Manon — 代码智能工具规则

当用户提问涉及代码理解、架构分析、代码搜索时，必须使用 Manon MCP 工具。

**默认深度查询**：所有代码理解查询使用 `manon_deep_query`（自动多轮迭代）

| 场景 | 工具 |
|------|------|
| 代码理解/搜索 | `manon_deep_query` |
| 调用关系/依赖 | `manon_graph` |
| 改动影响 | `manon_impact` |

**规则**：先图谱后搜索，图谱不足时声明"图谱未覆盖，补充搜索"'

# ══════════════════════════════════════════════════════
#  Platform detection
# ══════════════════════════════════════════════════════

detect_platforms() {
    PLATFORMS=()

    # Claude Code
    if [ -d "$HOME/.claude" ] || command -v claude >/dev/null 2>&1; then
        PLATFORMS+=("claude-code")
    fi

    # Cursor
    if [ -d "$HOME/.cursor" ]; then
        PLATFORMS+=("cursor")
    fi

    # Windsurf
    if [ -d "$HOME/.codeium/windsurf" ] || [ -d "$HOME/.windsurf" ]; then
        PLATFORMS+=("windsurf")
    fi

    # Zed
    if [ -d "$HOME/.config/zed" ] || command -v zed >/dev/null 2>&1; then
        PLATFORMS+=("zed")
    fi

    # Continue
    if [ -d "$HOME/.continue" ]; then
        PLATFORMS+=("continue")
    fi

    # CodeBuddy (Tencent)
    if [ -d "$HOME/.codebuddy" ] || [ -d "$HOME/.tencent/codebuddy" ]; then
        PLATFORMS+=("codebuddy")
    fi

    # OpenCode
    if [ -d "$HOME/.config/opencode" ] || command -v opencode >/dev/null 2>&1; then
        PLATFORMS+=("opencode")
    fi

    # Codex (OpenAI)
    if [ -d "$HOME/.codex" ] || command -v codex >/dev/null 2>&1; then
        PLATFORMS+=("codex")
    fi
}

# ══════════════════════════════════════════════════════
#  MCP config writers (one per platform)
# ══════════════════════════════════════════════════════

# --- helper: merge manon MCP entry into a JSON file ---
write_mcp_json() {
    local target_file="$1"
    $VENV_PYTHON - "$target_file" "$LAUNCHER_NORM" "$API_URL" "$API_KEY" <<'PYEOF'
import json, sys, os
target, launcher, url, key = sys.argv[1:5]
cfg = {}
if os.path.exists(target):
    with open(target, "r", encoding="utf-8") as f:
        cfg = json.load(f)
cfg.setdefault("mcpServers", {})
env = {"MANON_API_KEY": key}
if url != "auto":
    env["MANON_API_URL"] = url
cfg["mcpServers"]["manon"] = {
    "command": "bash",
    "args": [launcher],
    "env": env,
}
if "playwright" not in cfg["mcpServers"]:
    cfg["mcpServers"]["playwright"] = {
        "command": "npx",
        "args": ["@playwright/mcp@latest"],
    }
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
PYEOF
}

# --- Claude Code ---
configure_claude_code() {
    local settings="$HOME/.claude.json"
    local skill_dir="$HOME/.claude/skills/manon"

    # MCP config (write to ~/.claude.json — highest priority for Claude Code)
    write_mcp_json "$settings"
    info "Claude Code MCP registered"

    # /manon Skill (Claude Code exclusive)
    mkdir -p "$skill_dir/scripts"
    cp "$SCRIPT_DIR/skills/manon/SKILL.md" "$skill_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/manon/scripts/"*.py "$skill_dir/scripts/"
    info "Claude Code /manon Skill installed"

    # Install Claude Code hooks (PreToolUse + PostToolUse)
    "$VENV_PYTHON" - "$SCRIPT_DIR" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from manon_mcp._hooks import _install_claude_hooks
result = _install_claude_hooks()
PYEOF
    info "Claude Code hooks installed (search/edit/agent/commit→impact)"

    # Install dao skill (大道至简 - code simplification)
    local dao_skill_dir="$HOME/.claude/skills/dao"
    mkdir -p "$dao_skill_dir/scripts" "$dao_skill_dir/references"
    cp "$SCRIPT_DIR/skills/dao/SKILL.md" "$dao_skill_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/dao/scripts/"*.py "$dao_skill_dir/scripts/"
    cp "$SCRIPT_DIR/skills/dao/references/"*.md "$dao_skill_dir/references/"
    info "Claude Code /dao Skill installed (code simplification with Manon)"

    # Install experience skill (体验驱动开发循环)
    local exp_skill_dir="$HOME/.claude/skills/experience"
    mkdir -p "$exp_skill_dir"
    cp "$SCRIPT_DIR/skills/experience/SKILL.md" "$exp_skill_dir/SKILL.md"
    info "Claude Code /experience Skill installed (experience-driven dev loop)"

    # /tc 已退役（1.5.0）：覆盖循环并进 /assurance 的 P5。
    # 装过老版本的机器上 ~/.claude/skills/tc 还留着，这里主动摘掉——
    # 留一个不再被任何文档指向的壳，下一个人会以为它还在维护。
    rm -rf "$HOME/.claude/skills/tc"

    # Install idea skill (需求精化循环)
    local idea_skill_dir="$HOME/.claude/skills/idea"
    mkdir -p "$idea_skill_dir/scripts"
    cp "$SCRIPT_DIR/skills/idea/SKILL.md" "$idea_skill_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/idea/scripts/"*.py "$idea_skill_dir/scripts/"
    info "Claude Code /idea Skill installed (idea refinement with Manon)"

    # Install audit skill (行为层体检)
    local audit_skill_dir="$HOME/.claude/skills/audit"
    mkdir -p "$audit_skill_dir/references"
    cp "$SCRIPT_DIR/skills/audit/SKILL.md" "$audit_skill_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/audit/references/"*.md "$audit_skill_dir/references/"
    info "Claude Code /audit Skill installed (behaviour-layer audit with contract tables)"

    # Install retire-checks skill (退役过期的检查器 —— /assurance 分诊的去处之一)
    local retire_skill_dir="$HOME/.claude/skills/retire-checks"
    mkdir -p "$retire_skill_dir/references" "$retire_skill_dir/scripts"
    cp "$SCRIPT_DIR/skills/retire-checks/SKILL.md" "$retire_skill_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/retire-checks/references/"*.md "$retire_skill_dir/references/"
    cp "$SCRIPT_DIR/skills/retire-checks/scripts/"*.py "$retire_skill_dir/scripts/"
    info "Claude Code /retire-checks Skill installed (retire stale gates and test corpora)"

    # Install assurance skill (工程保障体系体检与补齐 —— 体系的入口，按读数分诊到 /audit /dao /retire-checks)
    # 注意：这个 skill 有 references/ 与 scripts/，两者都必须装。
    # 只装 SKILL.md 会留下一个链向不存在文件的入口，而且**没有任何报错**。
    local assurance_skill_dir="$HOME/.claude/skills/assurance"
    mkdir -p "$assurance_skill_dir/references" "$assurance_skill_dir/scripts"
    cp "$SCRIPT_DIR/skills/assurance/SKILL.md" "$assurance_skill_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/assurance/references/"*.md "$assurance_skill_dir/references/"
    cp "$SCRIPT_DIR/skills/assurance/scripts/"*.py "$assurance_skill_dir/scripts/"
    info "Claude Code /assurance Skill installed (four-layer assurance stack: audit, gap-fill, coverage loop)"
}

# --- Cursor ---
configure_cursor() {
    local mcp_file="$HOME/.cursor/mcp.json"
    local rules_dir="$HOME/.cursor/rules"

    # MCP config
    write_mcp_json "$mcp_file"
    info "Cursor MCP registered"

    # Global rules
    mkdir -p "$rules_dir"
    echo "$MANON_RULES" > "$rules_dir/manon.md"
    info "Cursor deep-query rules installed → $rules_dir/manon.md"
}

# --- Windsurf ---
configure_windsurf() {
    local mcp_file
    if [ -d "$HOME/.codeium/windsurf" ]; then
        mcp_file="$HOME/.codeium/windsurf/mcp_config.json"
    else
        mcp_file="$HOME/.windsurf/mcp_config.json"
    fi
    local rules_dir="$HOME/.windsurf/rules"

    # MCP config
    write_mcp_json "$mcp_file"
    info "Windsurf MCP registered"

    # Global rules
    mkdir -p "$rules_dir"
    echo "$MANON_RULES" > "$rules_dir/manon.md"
    info "Windsurf deep-query rules installed → $rules_dir/manon.md"
}

# --- Zed ---
configure_zed() {
    local settings="$HOME/.config/zed/settings.json"
    $VENV_PYTHON - "$settings" "$LAUNCHER_NORM" "$API_URL" "$API_KEY" <<'PYEOF'
import json, sys, os
target, launcher, url, key = sys.argv[1:5]
cfg = {}
if os.path.exists(target):
    with open(target, "r", encoding="utf-8") as f:
        cfg = json.load(f)
cfg.setdefault("context_servers", {})
env = {"MANON_API_KEY": key}
if url != "auto":
    env["MANON_API_URL"] = url
cfg["context_servers"]["manon"] = {
    "command": {"path": "bash", "args": [launcher], "env": env}
}
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
PYEOF
    info "Zed MCP registered"
}

# --- Continue ---
configure_continue() {
    local cfg_file="$HOME/.continue/config.json"
    $VENV_PYTHON - "$cfg_file" "$LAUNCHER_NORM" "$API_URL" "$API_KEY" <<'PYEOF'
import json, sys, os
target, launcher, url, key = sys.argv[1:5]
cfg = {}
if os.path.exists(target):
    with open(target, "r", encoding="utf-8") as f:
        cfg = json.load(f)
cfg.setdefault("mcpServers", [])
env = {"MANON_API_KEY": key}
if url != "auto":
    env["MANON_API_URL"] = url
cfg["mcpServers"] = [s for s in cfg["mcpServers"] if s.get("name") != "manon"]
cfg["mcpServers"].append({"name": "manon", "command": "bash", "args": [launcher], "env": env})
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
PYEOF
    info "Continue MCP registered"
}

# --- CodeBuddy (Tencent) ---
configure_codebuddy() {
    local mcp_file skill_dir
    if [ -d "$HOME/.codebuddy" ]; then
        mcp_file="$HOME/.codebuddy/mcp.json"
        skill_dir="$HOME/.codebuddy/skills/manon"
    else
        mcp_file="$HOME/.tencent/codebuddy/mcp.json"
        skill_dir="$HOME/.tencent/codebuddy/skills/manon"
    fi
    write_mcp_json "$mcp_file"
    info "CodeBuddy MCP registered"

    # /manon Skill
    mkdir -p "$skill_dir"
    cp "$SCRIPT_DIR/skills/manon/SKILL.md" "$skill_dir/SKILL.md"
    info "CodeBuddy /manon Skill installed"
}



# --- OpenCode ---
configure_opencode() {
    local cfg_file="$HOME/.config/opencode/opencode.json"

    # MCP config (OpenCode uses different format: type+command array+environment)
    $VENV_PYTHON - "$cfg_file" "$LAUNCHER_NORM" "$API_URL" "$API_KEY" <<'PYEOF'
import json, sys, os
target, launcher, url, key = sys.argv[1:5]
cfg = {}
if os.path.exists(target):
    with open(target, "r", encoding="utf-8") as f:
        cfg = json.load(f)
cfg.setdefault("mcp", {})
env = {"MANON_API_KEY": key}
if url != "auto":
    env["MANON_API_URL"] = url
cfg["mcp"]["manon"] = {
    "type": "local",
    "command": ["bash", launcher],
    "environment": env,
}
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
PYEOF
    info "OpenCode MCP registered"

    # Skill — OpenCode reads ~/.claude/skills/**/SKILL.md automatically
    # so the Claude Code skill covers OpenCode too. Install it if not present.
    local skill_dir="$HOME/.claude/skills/manon"
    if [ ! -f "$skill_dir/SKILL.md" ]; then
        mkdir -p "$skill_dir"
        cp "$SCRIPT_DIR/skills/manon/SKILL.md" "$skill_dir/SKILL.md"
        info "OpenCode /manon Skill installed (via ~/.claude/skills/)"
    else
        info "OpenCode Skill already present (shared with Claude Code)"
    fi
}



# --- Codex (OpenAI) ---
configure_codex() {
    local config_file="$HOME/.codex/config.toml"
    local agents_file="$HOME/AGENTS.md"
    local codex_command="bash"
    local codex_args="\"$LAUNCHER_NORM\""

    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            codex_command="$VENV_PYTHON_NORM"
            codex_args="\"$SERVER_PY_NORM\""
            ;;
    esac

    # MCP config — append [mcp_servers.manon] to config.toml if not present
    if grep -q '\[mcp_servers\.manon\]' "$config_file" 2>/dev/null; then
        info "Codex MCP already configured"
    else
        cat >> "$config_file" <<TOMLEOF

[mcp_servers.manon]
command = "$codex_command"
args = [$codex_args]
env = { MANON_API_KEY = "$API_KEY" }
startup_timeout_sec = 30.0
tool_timeout_sec = 120.0
TOMLEOF
        info "Codex MCP registered"
    fi

    # AGENTS.md — Manon rules (equivalent to Claude Code hooks)
    if [ -f "$agents_file" ] && grep -q "manon_search" "$agents_file" 2>/dev/null; then
        info "Codex AGENTS.md already has Manon rules"
    else
        cat >> "$agents_file" <<'AGENTSEOF'

# Codex AGENTS.md — Manon 知识图谱规则

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
- 同时用 `git log --oneline -10 -- <file>` 查看近期改动
- 非代码文件（.json/.yaml/.md/.toml 等）不受此限制

### 规则 3：探索代码库前必查图谱

在进行大范围代码探索或规划前，**必须先用 manon_search / manon_deep_query 查询图谱**。
图谱不足时才进行文件级探索，并声明"图谱未覆盖，补充搜索"。

## 执行顺序

1. **先图谱，再补搜索**：不足时才用文件搜索，且声明"图谱未覆盖，补充搜索"
2. **改代码前必查**：先 `manon_search` 或 `manon_graph` 了解上下文
3. **查不到时**：`manon_repos_list` 返回空 → `manon_init`
4. **改前看近史**：修改前先 `git log --oneline -10 -- <file>` 或 `manon_impact` 查最近改动
AGENTSEOF
        info "Codex AGENTS.md rules installed → $agents_file"
    fi
}



echo ""
echo "  Manon MCP — 代码智能工具"
echo "  ────────────────────────"
echo ""

# ── Python check / auto-install ───────────────────────
find_python() {
    # Check PATH first
    command -v python3 2>/dev/null && return 0
    command -v python 2>/dev/null && return 0
    # Check well-known locations (brew, system)
    for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        [ -x "$p" ] && echo "$p" && return 0
    done
    return 1
}

if ! find_python >/dev/null 2>&1; then
    warn "Python not found, attempting to install..."
    case "$(uname -s)" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                brew install python@3.12 || err "Failed to install Python. Install manually: https://python.org/downloads"
                hash -r 2>/dev/null || true
            else
                err "Python 3.10+ required. Install Homebrew first (https://brew.sh) or download from https://python.org/downloads"
            fi
            ;;
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                sudo apt-get install -y python3 python3-venv || err "Failed to install Python"
            elif command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y python3 || err "Failed to install Python"
            elif command -v yum >/dev/null 2>&1; then
                sudo yum install -y python3 || err "Failed to install Python"
            else
                err "Python 3.10+ required. Install via: https://python.org/downloads"
            fi
            ;;
        *)
            err "Python 3.10+ required. Install via: https://python.org/downloads"
            ;;
    esac
fi
PYTHON=$(find_python) || err "Python still not found after install. Please install Python 3.10+ manually: https://python.org/downloads"
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")
[ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ] || err "Python 3.10+ required (found $PY_MAJOR.$PY_MINOR)"
info "Python $PY_MAJOR.$PY_MINOR"

# ── Detect platforms ──────────────────────────────────
detect_platforms
if [ ${#PLATFORMS[@]} -eq 0 ]; then
    err "No supported platform detected (Claude Code / Cursor / Windsurf / OpenCode / Codex)"
fi
info "Detected: ${PLATFORMS[*]}"

# ── Config (fully automatic) ──────────────────────────
API_KEY=""

# ── Check for existing key ────────────────────────────
for _cfg in "$HOME/.claude.json" "$HOME/.claude/settings.json" "$HOME/.cursor/mcp.json" \
            "$HOME/.codeium/windsurf/mcp_config.json" "$HOME/.windsurf/mcp_config.json" \
            "$HOME/.config/opencode/opencode.json" "$HOME/.codex/config.toml"; do
    if [ -f "$_cfg" ]; then
        _key=$(python3 -c "
import json, sys, re
f = sys.argv[1]
try:
    if f.endswith('.toml'):
        text = open(f, encoding='utf-8').read()
        m = re.search(r'MANON_API_KEY\s*=\s*\"(msk_[^\"]+)\"', text)
        if m: print(m.group(1))
    else:
        d = json.load(open(f, encoding='utf-8'))
        k = d.get('mcpServers', {}).get('manon', {}).get('env', {}).get('MANON_API_KEY', '')
        if not k:
            k = d.get('mcp', {}).get('manon', {}).get('environment', {}).get('MANON_API_KEY', '')
        if k.startswith('msk_'): print(k)
except: pass
" "$_cfg" 2>/dev/null)
        if [ -n "$_key" ]; then
            API_KEY="$_key"
            info "Existing API key found, skipping registration"
            break
        fi
    fi
done

# ── Git remote ────────────────────────────────────────
GIT_REMOTE="https://github.com/brandonzyy/manon.git"
GIT_BRANCH="master"
cd "$SCRIPT_DIR" && git remote set-url origin "$GIT_REMOTE" 2>/dev/null || true
info "Git remote → $GIT_REMOTE"
API_URL="$API_URL_CN"

# ── Venv + deps ───────────────────────────────────────
head1 "Dependencies"
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
fi
if [ -f "$VENV_DIR/bin/python" ]; then
    VENV_PYTHON="$VENV_DIR/bin/python"
elif [ -f "$VENV_DIR/Scripts/python.exe" ]; then
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
else
    err "Failed to locate venv python"
fi
REQ_FILE="$SCRIPT_DIR/manon_mcp/requirements.txt"
[ -f "$REQ_FILE" ] || err "requirements.txt not found: $REQ_FILE"
"$VENV_PYTHON" -m pip install -q -r "$REQ_FILE"
info "Dependencies installed"

# ── Auto-register if no key ───────────────────────────
if [ -z "$API_KEY" ]; then
    head1 "Auto-register"
    # use CN endpoint for registration (always reachable from both regions)
    REG_URL="$API_URL"
    REG_RESULT=$("$VENV_PYTHON" -c "
import httpx, json, sys
try:
    r = httpx.post('${REG_URL}/api/v1/register', json={'name': '$(whoami)'}, timeout=10)
    r.raise_for_status()
    data = r.json()
    print(data['api_key'])
except Exception as e:
    print(f'FAIL:{e}', file=sys.stderr)
    sys.exit(1)
" 2>&1) || true

    if [[ "$REG_RESULT" == msk_* ]]; then
        API_KEY="$REG_RESULT"
        info "Auto-registered, API key: ${API_KEY:0:12}..."
    else
        warn "Auto-register failed ($REG_RESULT) — you can set the key manually later"
        API_KEY=""
    fi
fi

# normalize paths for JSON
SERVER_PY_NORM=$(echo "$SERVER_PY" | sed 's|\\|/|g')
VENV_PYTHON_NORM=$(echo "$VENV_PYTHON" | sed 's|\\|/|g')
LAUNCHER_NORM=$(echo "$LAUNCHER" | sed 's|\\|/|g')

# ── Configure each platform ──────────────────────────
CONFIGURED=()
for platform in "${PLATFORMS[@]}"; do
    head1 "$platform"
    case "$platform" in
        claude-code) configure_claude_code ;;
        cursor)      configure_cursor ;;
        windsurf)    configure_windsurf ;;
        zed)         configure_zed ;;
        continue)    configure_continue ;;
        codebuddy)   configure_codebuddy ;;
        opencode)    configure_opencode ;;
        codex)       configure_codex ;;
    esac
    CONFIGURED+=("$platform")
done

# ── Verify connectivity ──────────────────────────────
head1 "Connectivity"
CHECK_URL="$API_URL"
HTTP_CODE=$("$VENV_PYTHON" -c "
import httpx
try:
    r = httpx.get('${CHECK_URL}/health', timeout=5)
    print(r.status_code)
except Exception as e:
    print(f'error: {e}')
" 2>&1) || true

if [ "$HTTP_CODE" = "200" ]; then
    info "API reachable ($API_URL)"
else
    warn "API not reachable ($HTTP_CODE) — start the server first"
fi

# ── Summary ───────────────────────────────────────────
MANON_VERSION=$("$VENV_PYTHON" -c "
from pathlib import Path
import subprocess
version_file = Path(r'$SCRIPT_DIR') / 'VERSION'
try:
    value = version_file.read_text(encoding='utf-8').strip()
    print(value if value else '1.0.0')
except Exception:
    r = subprocess.run(['git', 'rev-list', '--count', 'HEAD'], cwd=r'$SCRIPT_DIR', capture_output=True, text=True)
    print(f'1.0.{r.stdout.strip()}' if r.returncode == 0 else '1.0.0')
" 2>/dev/null) || MANON_VERSION="1.0.0"
echo ""
echo "  ────────────────────────────────────"
echo "  Manon v${MANON_VERSION} installed"
echo "  Configured: ${CONFIGURED[*]}"
echo ""
for p in "${CONFIGURED[@]}"; do
    case "$p" in
        claude-code) echo "  Claude Code:  type /manon to initialize" ;;
        cursor)      echo "  Cursor:       manon_deep_query available in Composer" ;;
        windsurf)    echo "  Windsurf:     manon_deep_query available in Cascade" ;;
        zed)         echo "  Zed:          manon tools available in Assistant" ;;
        continue)    echo "  Continue:     manon tools available in Chat" ;;
        codebuddy)   echo "  CodeBuddy:    type /manon to initialize" ;;
        opencode)    echo "  OpenCode:     type /manon to initialize" ;;
        codex)       echo "  Codex:        manon tools available via MCP" ;;
    esac
done
echo ""
echo "  ────────────────────────────────────"
echo ""
