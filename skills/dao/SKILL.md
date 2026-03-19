---
name: dao
description: 大道至简 - Universal code simplification using Manon knowledge graph (architecture → module → code)
trigger: /dao
---

# 大道至简 (Dao)

**Philosophy**: Simplicity is the ultimate sophistication.

不预设问题，让知识图谱告诉我们这个项目的复杂度在哪里。原则是分类词汇，不是检查清单。

## Prerequisites

Call `mcp__manon__manon_repos_list`. If it fails → show install instructions and STOP.

```bash
pip install manon
# Add to ~/.claude/settings.json → mcpServers → manon: { "command": "manon", "args": ["mcp"] }
```

---

## ⚠️ PATH RULES (read before any script call)

The skill's Python scripts are **globally installed** — they are NOT inside the project directory.

```
SKILL_DIR  = the "Base directory for this skill" shown in the system header above
             (e.g., C:\Users\zack_\.claude\skills\dao  or  ~/.claude/skills/dao)

SCRIPT  = <SKILL_DIR>/scripts/dao-report.py   ← global, manages issues.json
SCANNER = <SKILL_DIR>/scripts/dao-scan.py     ← global, reads graph health
PICKER  = <SKILL_DIR>/scripts/dao-pick.py     ← global, interactive selector
```

**`.dao/` in the project** = data directory (issues.json, quality-report.md) — NOT the scripts.

Always call scripts as:
```
python <SKILL_DIR>/scripts/dao-report.py <args>
```
NEVER as `python .dao/...` or `python dao-report.py` without the full path.

---

## Execution Flow

1. `mcp__manon__manon_init(project_path)` → extract `repo_id`, `MANON_DIR`, `MANON_PYTHON`

2. **Analyze** — graph-driven, not pattern-driven:
   - `python SCANNER context <project_path> <repo_id>` → JSON `{health, scan_checklist, report_exists, open_issues, changed_files}`
   - `scan_checklist` contains all 19 principles (A1-A7, M1-M4, C1-C8); `priority: "high"` means a health dimension scored below threshold — investigate these first
   - **First run** (`report_exists=false`): `mcp__manon__manon_deep_query(repo_id, "这个项目的复杂度、耦合和重复主要集中在哪里？有哪些可以简化的机会？")` → cover all 19 principles in the inquiry, prioritize high-signal ones → `python SCRIPT init <project_path>` → `python SCRIPT add` each finding
   - **Subsequent runs** (`report_exists=true`): load open issues; use `changed_files` from scanner + re-run inquiry over changed areas; cover all 19 principles but focus high-priority ones → `python SCRIPT add` new findings

3. **Show panel + ask** — use `AskUserQuestion` to present issues and wait for selection:
   - List open issues grouped by layer (A / M / C) with index numbers
   - Ask: "选择要处理的 issue（输入编号），或输入 C 自动执行所有代码层问题"
   - Map answer → issue id → proceed to step 4 or 5

4. **User picks A/M issue** (e.g. `A1`, `M2`):
   - **Before** `EnterPlanMode`, create two tasks with exact commands filled in:
     ```
     TaskCreate(title="[DAO-POST 1/2] Sync graph",
                description="Run: \"<MANON_PYTHON>\" \"<SKILL_DIR>/scripts/manon-scan.py\" <repo_id>
     then: manon_scan_files(<repo_id>) → manon_upload_batch until done → manon_upload_coverage")
     TaskCreate(title="[DAO-POST 2/2] Close issue",
                description="Run: python \"<SKILL_DIR>/scripts/dao-report.py\" done <project_path> <issue_id> <commit_hash>")
     ```
   - `EnterPlanMode` → plan must include this closing section verbatim:
     ```
     ## 执行后（TaskList 中已登记，ExitPlanMode 后立即执行）
     - [ ] [DAO-POST 1/2] Sync Manon graph
     - [ ] [DAO-POST 2/2] python dao-report.py done <project_path> <id> <commit>
     ```
   - Wait for user approval → execute → one commit
   - `ExitPlanMode`
   - **← hook fires here: you will see a POST-PLAN PROTOCOL reminder**
   - Immediately call `TaskList` → complete tasks in order:
     1. Sync graph: run scan script → `manon_scan_files` → loop `manon_upload_batch` until done → `manon_upload_coverage` → `TaskUpdate([DAO-POST 1/2], completed)`
     2. Close issue: `python SCRIPT done <project_path> <id> <commit_hash>` → `TaskUpdate([DAO-POST 2/2], completed)`
   - Return to step 3

5. **User picks C** → auto-loop until no C candidates:
   - Validate before any merge/delete:
     - C2 merges: 3-question gate (git history check · op-type parity · name accuracy after merge)
     - Any delete: `manon_graph` zero-callers confirmed
     - Fail → skip, next candidate
   - Read → Simplify → Commit
   - Sync graph (same as step 4)
   - Post-check: fn ≥6 params or >60 lines → `python SCRIPT add <project_path> C <code> "<desc>"`
   - `python SCRIPT done <project_path> <id> <commit_hash>`
   - Continue loop

---

## Health Score → Layer Mapping

Use `dao-scan.py context` scores to focus the inquiry — not to dictate what to fix (coupling may be by design).

| Dimension | Score < threshold | Investigate |
|-----------|-------------------|-------------|
| MC 模块耦合 | < 9 | Cross-module deps — is this intentional architecture or accidental? |
| DC 死代码 | < 10 | Likely C4 candidates — verify with `manon_graph` callers |
| FS 函数规模 | < 9 | Oversized functions — C-layer complexity |
| TD 技术债务 | < 9 | TODOs, any_count — C-layer debt |
| FI 扇入集中度 | < 9 | Hot modules taking too much responsibility — M1 or A1 |
| CD 循环依赖 | < 10 | C7 or A1 — architectural cycle |
| TC 测试覆盖 | < 9 | Risky areas to simplify — proceed with caution |

---

## Principle Taxonomy

Classification vocabulary for findings. Assign a code when recording issues.

**Architecture (A)** — system-level structure:
- A1 Unnecessary layers · A2 Over-modularization · A3 Premature generalization
- A4 Over-decoupling · A5 Config complexity · A6 Event system overkill · A7 Over-patterning

**Module (M)** — module responsibilities and boundaries:
- M1 Feature bloat · M2 Unclear boundaries · M3 Duplication · M4 Excessive dependencies

**Code (C)** — file and function level:
- C1 Indirection/barrel · C2 Over-fragmentation · C3 Deep directories · C4 Dead code
- C5 Split by tech layer · C6 Unnecessary abstraction · C7 Circular deps · C8 Low cohesion

---

## Constraints

1. Never break functionality; stop if tests fail
2. One principle per commit; sync Manon graph after each commit
3. Architecture/module: single issue per session, Plan mode, human approval required
4. Code layer: auto-loop, no confirmation needed
5. C2: max 1 merge per loop iteration; fewer files ≠ simpler (result must be describable in one sentence)
6. Coupling or structure that appears intentional → do NOT simplify without asking
