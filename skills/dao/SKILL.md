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

3. **Show panel + ask** — use `AskUserQuestion`:
   - Show only **A and M** open issues (grouped, with index numbers) — do NOT list C issues individually
   - If there are open C issues, show only the count: "代码层 (C): N 个待处理，将自动批量执行"
   - Ask: "选择要处理的架构/模块 issue（输入编号），或直接回车跳过进入代码层自动优化"
   - If user picks A/M → step 4; if user skips/enters → step 5

4. **User picks A/M issue** (e.g. `A1`, `M2`):
   - `EnterPlanMode` — plan MUST start with this header (PreToolUse hook auto-writes marker from it):
     ```
     DAO: project=<project_path> issue=<issue_id> skill=<SKILL_DIR> repo=<repo_id>
     ```
   - Plan covers implementation only after the header; no post-steps in the document
   - Wait for user approval → `ExitPlanMode`
   - Execute implementation → run tests
   - `MANON_DAO_MSG="<commit message>" python "<SKILL_DIR>/scripts/dao-commit.py" "<project_path>" "<issue_id>" "<SKILL_DIR>" "<repo_id>"`
   - `manon_impact(repo_id, commit='HEAD')` → sync graph
   - Return to step 3

5. **C auto-loop** → process all C candidates without stopping:
   - For each candidate, validate first:
     - C2 merges: 3-question gate (git history · op-type parity · name accuracy)
     - Any delete: `manon_graph` zero-callers confirmed
     - Fail → skip, next candidate
   - Implement → `git commit` → **`manon_impact HEAD`** → **`python SCRIPT done <project_path> <id> <commit_hash>`** → sync graph — all in one response per issue
   - Post-check after commit: fn ≥6 params or >60 lines → `python SCRIPT add <project_path> C <code> "<desc>"`
   - Continue to next candidate

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
