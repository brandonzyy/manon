#!/usr/bin/env bash
set -euo pipefail

# ── Manon MCP Installer ──────────────────────────────
# Multi-platform installer for Manon code intelligence:
#   1. Python venv + dependencies
#   2. MCP server registration (Claude Code / Cursor / Windsurf)
#   3. Deep-query behavior rules for each platform
# ─────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_PY="$SCRIPT_DIR/run_mcp.py"
VENV_DIR="$SCRIPT_DIR/.venv"
DEFAULT_API_URL="http://117.131.45.179:3700"

# ── Colors ────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[x]${NC} $1"; exit 1; }
head1() { echo -e "\n${CYAN}── $1 ──${NC}"; }

# ── Deep-query rules (shared across platforms) ────────
MANON_RULES='# Manon — 代码智能工具规则

## 工具使用规则（MUST）

当用户提问涉及代码理解、架构分析、代码搜索时，必须使用 Manon MCP 工具。

### 默认深度查询

**所有涉及代码理解的查询，必须使用 `manon_deep_query`**，不使用 `manon_search`。

manon_deep_query 自动执行多轮迭代：拆解子问题 → 检查覆盖 → 补充查询 → 确保完整。
这保证每次查询都能获取到回答问题所需的全部代码上下文。

| 场景 | 工具 |
|------|------|
| 任何代码理解/搜索 | `manon_deep_query` |
| 查调用关系/依赖 | `manon_graph` |
| 评估改动影响 | `manon_impact` |
| 任务规划 | `manon_pipeline_start` |

### 每轮对话自动查询

每轮用户对话如果涉及代码相关内容（提问、修改、分析），
必须先调用 `manon_deep_query` 获取完整上下文，再回答或动手。

### 禁止跳过

- 禁止跳过图谱直接搜索文件
- 图谱不足时才可补充搜索，且必须声明"图谱未覆盖，补充搜索"'

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
}

# ══════════════════════════════════════════════════════
#  MCP config writers (one per platform)
# ══════════════════════════════════════════════════════

# --- helper: merge manon MCP entry into a JSON file ---
write_mcp_json() {
    local target_file="$1"
    $VENV_PYTHON - "$target_file" "$VENV_PYTHON_NORM" "$SERVER_PY_NORM" "$API_URL" "$API_KEY" <<'PYEOF'
import json, sys, os
target, venv_py, server, url, key = sys.argv[1:6]
cfg = {}
if os.path.exists(target):
    with open(target, "r", encoding="utf-8") as f:
        cfg = json.load(f)
cfg.setdefault("mcpServers", {})
env = {"MANON_API_KEY": key}
if url != "auto":
    env["MANON_API_URL"] = url
cfg["mcpServers"]["manon"] = {
    "command": venv_py,
    "args": [server],
    "env": env,
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
    mkdir -p "$skill_dir"
    cat > "$skill_dir/SKILL.md" <<SKILLEOF
---
name: manon
description: /manon — 进入 Manon 模式，初始化项目知识图谱连接
user_invocable: true
---

$MANON_RULES

## 初始化流程（/manon 触发时执行）

1. 调用 \`manon_init\`，传入当前工作目录路径和项目名
2. 根据返回结果：
   - 仓库已存在且已索引 → 展示图谱统计
   - 仓库已存在但未索引 → 轮询 \`manon_index_status\` 直到完成
   - 仓库不存在 → 已自动创建并触发索引，轮询等待
3. 调用 \`manon_config\` 展示当前配置
4. 告知用户 Manon 模式已激活
SKILLEOF
    info "Claude Code /manon Skill installed"
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
    $VENV_PYTHON - "$settings" "$VENV_PYTHON_NORM" "$SERVER_PY_NORM" "$API_URL" "$API_KEY" <<'PYEOF'
import json, sys, os
target, venv_py, server, url, key = sys.argv[1:6]
cfg = {}
if os.path.exists(target):
    with open(target, "r", encoding="utf-8") as f:
        cfg = json.load(f)
cfg.setdefault("context_servers", {})
env = {"MANON_API_KEY": key}
if url != "auto":
    env["MANON_API_URL"] = url
cfg["context_servers"]["manon"] = {
    "command": {"path": venv_py, "args": [server], "env": env}
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
    $VENV_PYTHON - "$cfg_file" "$VENV_PYTHON_NORM" "$SERVER_PY_NORM" "$API_URL" "$API_KEY" <<'PYEOF'
import json, sys, os
target, venv_py, server, url, key = sys.argv[1:6]
cfg = {}
if os.path.exists(target):
    with open(target, "r", encoding="utf-8") as f:
        cfg = json.load(f)
cfg.setdefault("mcpServers", [])
env = {"MANON_API_KEY": key}
if url != "auto":
    env["MANON_API_URL"] = url
cfg["mcpServers"] = [s for s in cfg["mcpServers"] if s.get("name") != "manon"]
cfg["mcpServers"].append({"name": "manon", "command": venv_py, "args": [server], "env": env})
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
PYEOF
    info "Continue MCP registered"
}

# --- CodeBuddy (Tencent) ---
configure_codebuddy() {
    local mcp_file
    if [ -d "$HOME/.codebuddy" ]; then
        mcp_file="$HOME/.codebuddy/mcp.json"
    else
        mcp_file="$HOME/.tencent/codebuddy/mcp.json"
    fi
    write_mcp_json "$mcp_file"
    info "CodeBuddy MCP registered"
}



echo ""
echo "  Manon MCP — 代码智能工具"
echo "  ────────────────────────"
echo ""

# ── Python check / auto-install ───────────────────────
if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
    warn "Python not found, attempting to install..."
    case "$(uname -s)" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                brew install python@3.12 || err "Failed to install Python. Install manually: https://python.org/downloads"
            else
                err "Python 3.10+ required. Install via: https://python.org/downloads"
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
PYTHON=$(command -v python3 || command -v python)
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")
[ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ] || err "Python 3.10+ required (found $PY_MAJOR.$PY_MINOR)"
info "Python $PY_MAJOR.$PY_MINOR"

# ── Detect platforms ──────────────────────────────────
detect_platforms
if [ ${#PLATFORMS[@]} -eq 0 ]; then
    err "No supported platform detected (Claude Code / Cursor / Windsurf)"
fi
info "Detected: ${PLATFORMS[*]}"

# ── Config (fully automatic) ──────────────────────────
API_URL="auto"
API_KEY=""

# ── Check for existing key ────────────────────────────
for _cfg in "$HOME/.claude.json" "$HOME/.claude/settings.json" "$HOME/.cursor/mcp.json" \
            "$HOME/.codeium/windsurf/mcp_config.json" "$HOME/.windsurf/mcp_config.json"; do
    if [ -f "$_cfg" ]; then
        _key=$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding='utf-8'))
    k = d.get('mcpServers', {}).get('manon', {}).get('env', {}).get('MANON_API_KEY', '')
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

# ── Venv + deps ───────────────────────────────────────
head1 "Dependencies"
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
fi
if [ -f "$VENV_DIR/bin/python" ]; then
    VENV_PYTHON="$VENV_DIR/bin/python"
elif [ -f "$VENV_DIR/Scripts/python.exe" ]; then
    VENV_PYTHON="$VENV_DIR/Scripts/python"
else
    err "Failed to locate venv python"
fi
"$VENV_PYTHON" -m pip install -q -r "$SCRIPT_DIR/mcp/requirements.txt"
info "Dependencies installed"

# ── Auto-register if no key ───────────────────────────
if [ -z "$API_KEY" ]; then
    head1 "Auto-register"
    # use CN endpoint for registration (always reachable from both regions)
    REG_URL="$API_URL"
    [ "$REG_URL" = "auto" ] && REG_URL="$DEFAULT_API_URL"
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
    esac
    CONFIGURED+=("$platform")
done

# ── Verify connectivity ──────────────────────────────
head1 "Connectivity"
CHECK_URL="$API_URL"
[ "$CHECK_URL" = "auto" ] && CHECK_URL="$DEFAULT_API_URL"
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
echo ""
echo "  ────────────────────────────────────"
echo "  Done! Configured: ${CONFIGURED[*]}"
echo ""
for p in "${CONFIGURED[@]}"; do
    case "$p" in
        claude-code) echo "  Claude Code:  type /manon to initialize" ;;
        cursor)      echo "  Cursor:       manon_deep_query available in Composer" ;;
        windsurf)    echo "  Windsurf:     manon_deep_query available in Cascade" ;;
        zed)         echo "  Zed:          manon tools available in Assistant" ;;
        continue)    echo "  Continue:     manon tools available in Chat" ;;
        codebuddy)   echo "  CodeBuddy:    manon tools available in Chat" ;;
    esac
done
echo ""
echo "  ────────────────────────────────────"
echo ""
