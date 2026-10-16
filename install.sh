#!/usr/bin/env bash
set -euo pipefail

# ── Manon 本地安装器 ─────────────────────────────────
# 纯本地部署：venv + 依赖 + manon-kg 包装器（+ 可选 skills 安装）。
# 前置：Python 3.10+；git clone 本仓到任意目录（下称 REPO_ROOT）。
# 可选环境变量：
#   MANON_EMBEDDING_API_KEY  硅基流动 key（语义查询/向量索引）
#   VERISPRING_API_KEY       deep-query 的 LLM key（verispring deepseek-flash）

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
VENV="$REPO_ROOT/.venv"
INSTALL_SKILLS=0
[ "${1:-}" == "--install-skills" ] && INSTALL_SKILLS=1

echo "[1/4] Python 3.10+ 检测"
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        v=$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0)
        major=${v%%.*}; minor=${v#*.}
        if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ] 2>/dev/null; then PY="$c"; break; fi
    fi
done
[ -n "$PY" ] || { echo "需要 Python 3.10+"; exit 1; }
echo "  使用: $PY ($v)"

echo "[2/4] venv + 依赖"
if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install -q -r "$REPO_ROOT/requirements.txt"

echo "[3/4] manon-kg 包装器"
chmod +x "$REPO_ROOT/manon-kg"

echo "[4/4] skills"
if [ "$INSTALL_SKILLS" -eq 1 ]; then
    # 共享 skill 位：ZCode 与其他 agent 的用户级 skill 都读 ~/.agents/skills/
    base="$HOME/.agents/skills"
    mkdir -p "$base/manon" "$base/assurance/references" "$base/assurance/scripts" "$base/assurance/tests"
    cp "$REPO_ROOT/skills/manon/SKILL.md" "$base/manon/SKILL.md"
    cp "$REPO_ROOT/skills/assurance/SKILL.md" "$base/assurance/SKILL.md"
    cp "$REPO_ROOT/skills/assurance/references/"*.md "$base/assurance/references/"
    cp "$REPO_ROOT/skills/assurance/scripts/"*.py "$base/assurance/scripts/"
    cp "$REPO_ROOT/skills/assurance/tests/"*.py "$base/assurance/tests/"
    echo "  已安装 manon + assurance 到 $base"
else
    echo "  跳过（加 --install-skills 安装 manon/assurance 技能到 ~/.agents/skills）"
fi

echo
echo "安装完成。用法："
echo "  cd <你的项目> && $REPO_ROOT/manon-kg sync && $REPO_ROOT/manon-kg status"
echo "可选环境变量："
echo "  export MANON_EMBEDDING_API_KEY=sk-...   # 硅基流动（语义查询/向量索引，必需）"
echo "  export VERISPRING_API_KEY=sk-...        # verispring（deep-query 的 LLM）"
