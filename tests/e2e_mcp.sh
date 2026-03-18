#!/usr/bin/env bash
# Manon MCP E2E Test — tests all 14 tools via Claude Code -p mode
set -euo pipefail

# Prevent nested Claude Code session errors
unset CLAUDECODE 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANON_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MCP_CONFIG="$SCRIPT_DIR/mcp-config.json"
DONNIE_DIR="C:/Users/zack_/Desktop/一码行云/donnie"
LOG_DIR="$SCRIPT_DIR/e2e-logs"

# Read API key from config for curl-based polling
API_KEY=$(grep -o '"MANON_API_KEY": *"[^"]*"' "$MCP_CONFIG" | grep -o 'msk_[^"]*')
API_URL=$(grep -o '"MANON_API_URL": *"[^"]*"' "$MCP_CONFIG" | grep -o 'http[^"]*')

mkdir -p "$LOG_DIR"

PASS=0
FAIL=0
SKIP=0
REPO_ID=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

log_pass() { echo -e "${GREEN}[PASS]${NC} $1"; ((PASS++)); }
log_fail() { echo -e "${RED}[FAIL]${NC} $1: $2"; ((FAIL++)); }
log_skip() { echo -e "${YELLOW}[SKIP]${NC} $1: $2"; ((SKIP++)); }

# Run a claude -p command and capture output
run_test() {
    local name="$1"
    local prompt="$2"
    local log_file="$LOG_DIR/${name}.log"

    echo "--- Testing: $name ---"
    local output
    if output=$(cd "$DONNIE_DIR" && claude -p \
        --mcp-config "$MCP_CONFIG" \
        --dangerously-skip-permissions \
        "$prompt" 2>&1); then
        echo "$output" > "$log_file"
        echo "$output"
        return 0
    else
        echo "$output" > "$log_file"
        echo "$output"
        return 1
    fi
}

echo "========================================="
echo " Manon MCP E2E Test Suite"
echo "========================================="
echo ""

# ── 1. manon_config ──
if output=$(run_test "01_config" "调用 manon_config 查看配置，只返回工具的原始输出"); then
    if echo "$output" | grep -qi "LLM\|模型\|llm_model\|embedding"; then
        log_pass "manon_config"
    else
        log_fail "manon_config" "unexpected output"
    fi
else
    log_fail "manon_config" "command failed"
fi
echo ""

# ── 2. manon_repos_list ──
if output=$(run_test "02_repos_list" "调用 manon_repos_list 列出所有仓库，只返回工具的原始输出"); then
    log_pass "manon_repos_list"
else
    log_fail "manon_repos_list" "command failed"
fi
echo ""

# ── 3. manon_init ──
if output=$(run_test "03_init" \
    "调用 manon_init，project_path 设为 '$DONNIE_DIR'。只返回工具的原始输出，不要额外解释。"); then
    # Extract repo_id from output
    REPO_ID=$(echo "$output" | grep -oP 'id=\K[a-f0-9-]+' | head -1)
    if [ -z "$REPO_ID" ]; then
        REPO_ID=$(echo "$output" | grep -oP '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' | head -1)
    fi
    if [ -n "$REPO_ID" ]; then
        log_pass "manon_init (repo_id=$REPO_ID)"
    else
        log_fail "manon_init" "could not extract repo_id"
        echo "Output was: $output"
    fi
else
    log_fail "manon_init" "command failed"
fi
echo ""

if [ -z "$REPO_ID" ]; then
    echo "FATAL: No repo_id — cannot continue with repo-dependent tests."
    echo ""
    echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
    exit 1
fi

# ── 4. manon_index_status ──
if output=$(run_test "04_index_status" \
    "调用 manon_index_status，repo_id='$REPO_ID'。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "状态\|status\|done\|indexing\|pending"; then
        log_pass "manon_index_status"
    else
        log_fail "manon_index_status" "unexpected output"
    fi
else
    log_fail "manon_index_status" "command failed"
fi
echo ""

# ── 5. manon_repos_get ──
if output=$(run_test "05_repos_get" \
    "调用 manon_repos_get，repo_id='$REPO_ID'。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "id\|name\|index_status"; then
        log_pass "manon_repos_get"
    else
        log_fail "manon_repos_get" "unexpected output"
    fi
else
    log_fail "manon_repos_get" "command failed"
fi
echo ""

# Wait for indexing to complete before search/graph tests (use curl, not claude -p)
echo "--- Waiting for indexing (polling every 10s, max 120s) ---"
for i in $(seq 1 12); do
    status_out=$(curl -s -H "Authorization: Bearer $API_KEY" \
        "$API_URL/api/v1/repos/$REPO_ID/index-status" 2>&1 || true)
    if echo "$status_out" | grep -q '"status":"done"'; then
        echo "Indexing complete."
        break
    fi
    echo "  Still indexing... (attempt $i/12) $status_out"
    sleep 10
done
echo ""

# ── 6. manon_search ──
if output=$(run_test "06_search" \
    "调用 manon_search，repo_id='$REPO_ID'，query='electron main process'。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "实体\|entity\|找到\|score\|chunk\|代码\|未找到"; then
        log_pass "manon_search"
    else
        log_fail "manon_search" "no results found"
    fi
else
    log_fail "manon_search" "command failed"
fi
echo ""

# ── 7. manon_graph ──
if output=$(run_test "07_graph" \
    "调用 manon_graph，repo_id='$REPO_ID'，symbol='BrowserWindow'。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "图谱\|实体\|关系\|graph\|entity\|0 个"; then
        log_pass "manon_graph"
    else
        log_fail "manon_graph" "no graph data"
    fi
else
    log_fail "manon_graph" "command failed"
fi
echo ""

# ── 8. manon_impact ──
if output=$(run_test "08_impact" \
    "调用 manon_impact，repo_id='$REPO_ID'，commit='HEAD'。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "影响\|impact\|commit\|变更\|changed"; then
        log_pass "manon_impact"
    else
        log_fail "manon_impact" "unexpected output"
    fi
else
    log_fail "manon_impact" "command failed"
fi
echo ""

# ── 9. manon_deep_query ──
if output=$(run_test "09_deep_query" \
    "调用 manon_deep_query，repo_id='$REPO_ID'，question='donnie 的架构是怎样的'。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "架构\|module\|组件\|electron\|main\|renderer\|查询轮次\|Round"; then
        log_pass "manon_deep_query"
    else
        log_fail "manon_deep_query" "no architecture info"
    fi
else
    log_fail "manon_deep_query" "command failed"
fi
echo ""

# ── 10. manon_push_update ──
if output=$(run_test "10_push_update" \
    "调用 manon_push_update，repo_id='$REPO_ID'。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "同步\|sync\|变更\|没有\|no change\|已同步\|已触发\|indexing"; then
        log_pass "manon_push_update"
    else
        log_fail "manon_push_update" "unexpected output"
    fi
else
    log_fail "manon_push_update" "command failed"
fi
echo ""

# ── 11. manon_account ──
if output=$(run_test "11_account" \
    "调用 manon_account 查看账户信息。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "租户\|tenant\|配额\|quota\|仓库"; then
        log_pass "manon_account"
    else
        log_fail "manon_account" "unexpected output"
    fi
else
    log_fail "manon_account" "command failed"
fi
echo ""

# ── 12. manon_usage ──
if output=$(run_test "12_usage" \
    "调用 manon_usage 查看用量统计。只返回工具的原始输出。"); then
    log_pass "manon_usage"
else
    log_fail "manon_usage" "command failed"
fi
echo ""

# ── 13. manon_embedding ──
if output=$(run_test "13_embedding" \
    "调用 manon_embedding，texts 参数传 ['hello world', 'test embedding']。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "向量\|维度\|vector\|dimension\|生成"; then
        log_pass "manon_embedding"
    else
        log_fail "manon_embedding" "no embedding data"
    fi
else
    log_fail "manon_embedding" "command failed"
fi
echo ""

# ── 14. manon_repos_delete (cleanup) ──
if output=$(run_test "14_repos_delete" \
    "调用 manon_repos_delete，repo_id='$REPO_ID'。只返回工具的原始输出。"); then
    if echo "$output" | grep -qi "删除\|delete\|已删除"; then
        log_pass "manon_repos_delete"
    else
        log_fail "manon_repos_delete" "unexpected output"
    fi
else
    log_fail "manon_repos_delete" "command failed"
fi
echo ""

# ── Summary ──
echo "========================================="
echo " Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC}"
echo " Logs: $LOG_DIR/"
echo "========================================="

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
