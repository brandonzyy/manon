---
name: tc
description: 测试覆盖循环 — 扫描覆盖率、按图谱优先级补测试、验证、提交
trigger: /tc
---

# 测试覆盖循环 (TC)

**Philosophy**: 图谱指导优先级，lcov 衡量覆盖率，AI 编写测试。

## Prerequisites

Call `mcp__manon__manon_repos_list`. If it fails → show install instructions and STOP.

---

## ⚠️ PATH RULES

```
SKILL_DIR  = the "Base directory for this skill" shown in the system header above
SCANNER    = <SKILL_DIR>/scripts/tc-scan.py     ← 覆盖率扫描 + 优先级排序
COMMITTER  = <SKILL_DIR>/scripts/tc-commit.py   ← 提交测试 + 更新覆盖率
```

Always call scripts as `python <SKILL_DIR>/scripts/tc-scan.py <args>`.

---

## Execution Flow

1. **Init**
   - `manon_init(project_path)` → extract `repo_id`, `MANON_PYTHON`
   - Ensure coverage data exists: check for `coverage/lcov.info` in project or sub-packages
   - If no lcov found → run `bun test --coverage` (30s cap) to generate it

2. **Scan** — `python SCANNER scan <project_path> <repo_id>`
   - Parses lcov.info → per-file line/function coverage
   - Calls Manon graph API to get fan-in for uncovered functions
   - Outputs JSON: `{summary, targets}` sorted by priority (fan-in × uncovered-ratio)

3. **Show panel + ask** — use `AskUserQuestion`:
   - Show coverage summary (line%, function%, file%)
   - Show Top-10 priority targets (high fan-in + low coverage)
   - Ask: "选择要补测试的目标（输入编号），或回车进入自动模式"

4. **Auto-loop** (for each target):
   a. Read the source file → understand function signature, dependencies, types
   b. Check if `.test.ts` already exists → append or create
   c. Write test using `describe/test/expect` pattern (bun:test)
   d. Run `bun test <test_file>` → verify passes
   e. If test fails → fix (max 2 attempts), then skip if still failing
   f. `python COMMITTER <project_path> <test_file> <source_file>`
      - git add + commit with message "test(<module>): add tests for <function>"
      - Re-run `bun test --coverage` to update lcov
   g. Show coverage delta (before → after) → next target

5. **Finish**
   - Show final coverage summary vs initial
   - Update `.tc/coverage-report.md` with results

---

## Target Selection Strategy

Priority = fan_in_score × uncovered_weight

- **fan_in_score**: `manon_graph(symbol, direction=callers)` → number of callers
  - ≥ 5 callers: critical (3x weight)
  - 2-4 callers: important (2x weight)
  - 0-1 callers: normal (1x weight)
- **uncovered_weight**: based on lcov line coverage for that file
  - 0% covered: 3x
  - 1-50% covered: 2x
  - 51-80% covered: 1x
  - > 80% covered: skip

---

## Test Writing Rules

1. Import from the source file using relative paths
2. Use `describe/test/expect` from `bun:test`
3. Mock external dependencies (file system, network, database)
4. Test the public API, not internal implementation
5. Each test should be independent and idempotent
6. Name tests descriptively: `test("functionName handles edge case", ...)`
7. Cover: normal case, edge cases, error handling

---

## Constraints

1. Never modify source code — only create/modify test files
2. One source file's tests per commit
3. Skip if test can't pass after 2 fix attempts
4. Don't test trivial getters/setters or type-only files
5. Max 20 targets per session (avoid context window overflow)
