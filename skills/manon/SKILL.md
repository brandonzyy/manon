---
name: manon
description: /manon -- 进入 Manon 模式
user_invocable: true
---

# Manon 初始化

**核心规则**：
- 必须按顺序执行 Step 1-6，不跳步
- 工具返回的 `<!-- DISPLAY_VERBATIM -->` 内容必须原样输出给用户
- 扫描脚本必须用 `MANON_PYTHON`，不用系统 python

---

## Step 1: Init
`manon_init(project_path)` → 提取 `repo_id`

## Step 2: Smart Analysis
- 如果输出含 `<!-- SMART_ANALYSIS_DONE -->` → 跳到 Step 3
- 如果输出含 `<!-- SMART_ANALYSIS_NEEDED -->` → 执行：
  1. `manon_directory_signals(project_path)` 获取目录信号
  2. 根据【目录角色规则】判断每个目录 index/skip
  3. `manon_configure_excludes(project_path, ["**/skip_dir/**", ...])`
  4. 向用户展示分析结果

## Step 3: Scan & Upload
1. 从 Step 1 输出提取 `MANON_PYTHON`
2. Bash: `MANON_DIR="<MANON_DIR>" "<MANON_PYTHON>" "<SKILL_DIR>/scripts/manon-scan.py" <repo_id>`
   - `<SKILL_DIR>` = 本 skill 所在目录（`~/.claude/skills/manon`）
   - Windows bash 同样用此格式，**不要用** `set MANON_DIR=...`（CMD 语法，bash 里不生效）
   - 如报错（文件不存在）→ 运行 `bash "<MANON_DIR>/install.sh"` 后重试
3. `manon_scan_files(repo_id)`
4. 循环 `manon_upload_batch(repo_id)` 直到 status == "done"

## Step 4: Index Status
`manon_index_status(repo_id)` → **表格形式完整呈现**，不总结不省略

## Step 5: Code Health
`manon_code_health(repo_id)` → **表格形式完整呈现**，不总结不省略

## Step 6: Activate
告知用户 Manon 已激活，并说明 hooks 功能：
- **git push hook** - push 后自动更新图谱 + 打印健康评分
- **Claude Code hooks** - 强制 Manon 优先（Grep/Glob/Explore 前必须先查图谱，commit 后提示 impact）

---

## 目录角色规则（用于 Step 2）

**排除（skip）**：
`scripts/` `tools/` `bin/` `examples/` `demo/` `docs/` `assets/` `static/` `public/` `data/` `fixtures/` `config/`

**索引（index）**：
`src/` `lib/` `core/` `app/` `pkg/` `internal/` + 有 `__init__.py`/`package.json`/`Cargo.toml` 的目录

**不确定时**：源码文件占比 > 30% → index
