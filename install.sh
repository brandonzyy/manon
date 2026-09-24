#!/usr/bin/env bash
set -euo pipefail

# ── Manon MCP Installer ──────────────────────────────
# Multi-platform installer for Manon code intelligence:
#   1. Python venv + dependencies
#   2. MCP server registration (Claude Code / Codex / ZCode / Kimi Code)
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

# ══════════════════════════════════════════════════════
#  Platform detection
# ══════════════════════════════════════════════════════

detect_platforms() {
    PLATFORMS=()

    # Claude Code
    if [ -d "$HOME/.claude" ] || command -v claude >/dev/null 2>&1; then
        PLATFORMS+=("claude-code")
    fi

    # Codex (OpenAI)
    if [ -d "$HOME/.codex" ] || command -v codex >/dev/null 2>&1; then
        PLATFORMS+=("codex")
    fi

    # ZCode
    if [ -d "$HOME/.zcode" ] || command -v zcode >/dev/null 2>&1; then
        PLATFORMS+=("zcode")
    fi

    # Kimi Code (Moonshot)
    if [ -d "$HOME/.kimi-code" ] || command -v kimi >/dev/null 2>&1; then
        PLATFORMS+=("kimi-code")
    fi
}

# ══════════════════════════════════════════════════════
#  MCP config writers (one per platform)
# ══════════════════════════════════════════════════════

# --- helper: 把 manon 这一条 MCP 登记合进别家客户端的 JSON 配置 ---
# 只改 <容器>.manon 这一个键，别的服务器与键原样；读不懂原文件就不写（返回 3）；
# 写是原子的，内容没变不写。实现与判据：manon_mcp/_safe_config.py、tests/test_install_safety.py。
# 用法：write_mcp_json <文件> [容器，点分，缺省 mcpServers] [type]
write_mcp_json() {
    local target_file="$1" container="${2:-mcpServers}" kind="${3:-}"
    "$VENV_PYTHON" "$SCRIPT_DIR/manon_mcp/_safe_config.py" mcp "$target_file" "$container" \
        --api-key "$API_KEY" --api-url "$API_URL" ${kind:+--type "$kind"} \
        --command bash --arg "$LAUNCHER_NORM"
}

# --- Claude Code ---
configure_claude_code() {
    local settings="$HOME/.claude.json"
    local skill_dir="$HOME/.claude/skills/manon"

    # MCP config (write to ~/.claude.json — highest priority for Claude Code)
    if write_mcp_json "$settings"; then info "Claude Code MCP registered"
    else warn "Claude Code MCP 未登记：$settings 读不懂，原样未动"; fi

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

    # /assurance —— 工程保障体系的唯一入口（1.6.0 起 /dao、/audit、/retire-checks 并入，
    # /experience、/idea 退役）。注意它有 references/ 与 scripts/，两者都必须装：
    # 只装 SKILL.md 会留下一个链向不存在文件的入口，而且**没有任何报错**。
    local assurance_skill_dir="$HOME/.claude/skills/assurance"
    mkdir -p "$assurance_skill_dir/references" "$assurance_skill_dir/scripts" "$assurance_skill_dir/tests"
    cp "$SCRIPT_DIR/skills/assurance/SKILL.md" "$assurance_skill_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/assurance/references/"*.md "$assurance_skill_dir/references/"
    cp "$SCRIPT_DIR/skills/assurance/scripts/"*.py "$assurance_skill_dir/scripts/"
    cp "$SCRIPT_DIR/skills/assurance/tests/"*.py "$assurance_skill_dir/tests/"
    info "Claude Code /assurance Skill installed (assurance stack: gap-fill, coverage loop, behaviour audit, simplification, retirement)"

    # 已退役 skill 的壳主动摘掉（tc: 1.5.0；dao/audit/retire-checks/experience/idea: 1.6.0）。
    # 装过老版本的机器上它们还留着——留一个不再被任何文档指向的壳，
    # 下一个人会以为它还在维护。**只摘 manon 自己装的那一版**（SKILL.md 指纹对得上）：
    # 同名目录可能是用户自己的 skill 或改过的副本，那种保留并提示。
    "$VENV_PYTHON" "$SCRIPT_DIR/manon_mcp/_safe_config.py" retire-skills "$HOME/.claude/skills"
}

# --- Codex (OpenAI) ---
configure_codex() {
    local config_file="$HOME/.codex/config.toml"
    local agents_file="$HOME/.codex/AGENTS.md"
    local codex_command="bash"
    local codex_args="\"$LAUNCHER_NORM\""

    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            codex_command="$VENV_PYTHON_NORM"
            codex_args="\"$SERVER_PY_NORM\""
            ;;
    esac

    # MCP config — append [mcp_servers.manon] to config.toml if not present
    mkdir -p "$HOME/.codex"
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

    # Manon 调用规则 → ~/.codex/AGENTS.md（Codex 的全局指令文件；已有 manon_search 则不动）
    "$VENV_PYTHON" - "$SCRIPT_DIR" "$agents_file" <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from manon_mcp._hooks import _install_codex_agents_md
_install_codex_agents_md(Path(sys.argv[2]))
PYEOF
    info "Codex Manon rules → $agents_file"
}

# --- shared skill install → ~/.agents/skills ---
# ZCode 与 Kimi Code 的用户级 skill 都读 ~/.agents/skills/（zcode 官方推荐跨工具
# 共享位；kimi 的用户级 generic 目录）。装一份同时覆盖两个平台，不各自留副本。
install_agents_skills() {
    local base="$HOME/.agents/skills"

    local manon_dir="$base/manon"
    mkdir -p "$manon_dir/scripts"
    cp "$SCRIPT_DIR/skills/manon/SKILL.md" "$manon_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/manon/scripts/"*.py "$manon_dir/scripts/"

    # references/ 与 scripts/ 必须随 SKILL.md 一起装（同 ~/.claude/skills 的教训：
    # 只装 SKILL.md 会留下链向不存在文件的入口，且没有任何报错）
    local assurance_dir="$base/assurance"
    mkdir -p "$assurance_dir/references" "$assurance_dir/scripts" "$assurance_dir/tests"
    cp "$SCRIPT_DIR/skills/assurance/SKILL.md" "$assurance_dir/SKILL.md"
    cp "$SCRIPT_DIR/skills/assurance/references/"*.md "$assurance_dir/references/"
    cp "$SCRIPT_DIR/skills/assurance/scripts/"*.py "$assurance_dir/scripts/"
    cp "$SCRIPT_DIR/skills/assurance/tests/"*.py "$assurance_dir/tests/"

    # 已退役 skill 的壳同样从共享位摘掉（与 ~/.claude/skills 一致，只摘指纹对得上的）
    "$VENV_PYTHON" "$SCRIPT_DIR/manon_mcp/_safe_config.py" retire-skills "$base"
}

# --- ZCode ---
configure_zcode() {
    local config_file="$HOME/.zcode/cli/config.json"

    # MCP config — config.json 里还有 plugin 开关等状态，必须合并非覆盖；
    # server schema 是严格校验（未知键整条被丢弃），只写规范字段
    if write_mcp_json "$config_file" "mcp.servers" stdio; then info "ZCode MCP registered"
    else warn "ZCode MCP 未登记：$config_file 读不懂，原样未动"; fi

    install_agents_skills
    info "ZCode /manon + /assurance Skills installed (via ~/.agents/skills/)"
}

# --- Kimi Code (Moonshot) ---
configure_kimi_code() {
    # ~/.kimi-code/mcp.json 与 Claude 同格式（顶层 mcpServers）
    if write_mcp_json "$HOME/.kimi-code/mcp.json"; then info "Kimi Code MCP registered"
    else warn "Kimi Code MCP 未登记：~/.kimi-code/mcp.json 读不懂，原样未动"; fi

    install_agents_skills
    info "Kimi Code /manon + /assurance Skills installed (via ~/.agents/skills/)"
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
    err "No supported platform detected (Claude Code / Codex / ZCode / Kimi Code)"
fi
info "Detected: ${PLATFORMS[*]}"

# ── Config (fully automatic) ──────────────────────────
API_KEY=""

# ── Check for existing key ────────────────────────────
for _cfg in "$HOME/.claude.json" "$HOME/.claude/settings.json" "$HOME/.codex/config.toml" \
            "$HOME/.zcode/cli/config.json" "$HOME/.kimi-code/mcp.json"; do
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
        if not k:
            k = d.get('mcp', {}).get('servers', {}).get('manon', {}).get('env', {}).get('MANON_API_KEY', '')
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
        codex)       configure_codex ;;
        zcode)       configure_zcode ;;
        kimi-code)   configure_kimi_code ;;
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
        codex)       echo "  Codex:        manon tools available via MCP" ;;
        zcode)       echo "  ZCode:        type /manon to initialize" ;;
        kimi-code)   echo "  Kimi Code:    type /manon to initialize" ;;
    esac
done
echo ""
echo "  ────────────────────────────────────"
echo ""
