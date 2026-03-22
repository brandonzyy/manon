---
name: idea
description: 需求精化 — 图谱感知 + 技术调研 → 启发式追问 → 开发文档
user_invocable: true
---

# /idea — 需求精化

将模糊想法变成可执行的开发文档。**文档确认前禁止写任何代码。**

## ⚠️ PATH RULES

```
SKILL_DIR  = the "Base directory for this skill" shown in the system header above
MANON_PYTHON = from manon_init output <!-- MANON_PYTHON=... -->
```

## Prerequisites

Call `mcp__manon__manon_repos_list`. If empty → run `/manon` first, then restart `/idea`.
Extract `repo_id` from the matching project.

---

## Phase 1: CONTEXT（自动，不输出）

```bash
"<MANON_PYTHON>" "<SKILL_DIR>/scripts/idea-ctx.py" <repo_id> "<用户描述>"
```

解析 JSON，记住 modules/graph/health/research。不展示给用户。

## Phase 2: EXPLORE（启发式追问）

基于 Phase 1 的图谱事实 + GitHub 调研，向用户提问。

**规则：**
1. **每次用 `AskUserQuestion` 只问一个问题**
2. **优先选择题**（A/B/C），必要时才开放题
3. 每个问题附一行理由（图谱发现或调研发现）
4. 3-7 轮收敛，用户说"够了"可提前结束
5. 追问方向：功能边界、技术选型、兼容约束、优先级

**问题格式：**
```
[图谱发现] 模块 X 和 Y 有双向调用，新功能放哪边？
  A) 放 X（改动少，但 X 已经 fan-in 较高）
  B) 放 Y（需要新增接口，但职责更清晰）
  C) 新建模块（解耦彻底，但增加复杂度）
```

## Phase 3: PROPOSE（2-3 方案）

每个方案包含：
- 一句话概述
- 改动模块 + 影响链（引用 Phase 1 graph 数据）
- 优劣对比
- 对健康度的影响（引用 Phase 1 health.weak）

推荐一个，用户选定后进入 Phase 4。

## Phase 4: DOCUMENT（生成开发文档）

输出 Markdown 到 `docs/idea-<主题>.md`，必须包含以下章节：

```markdown
# <功能名称>

## 需求边界
- MUST: ...
- SHOULD: ...
- MAY: ...

## 技术方案
（选定方案详情，含模块交互描述）

## 改动文件
- `path/to/file.py` — 改动说明

## 风险点
| 风险 | 缓解措施 |
|------|---------|

## 验收标准
- [ ] 标准 1
- [ ] 标准 2
```

## Phase 5: REVIEW（自检）

```bash
"<MANON_PYTHON>" "<SKILL_DIR>/scripts/idea-check.py" "docs/idea-<主题>.md"
```

- `pass: true` → 展示文档，请用户审阅确认
- `pass: false` → 修复 issues 后重新检查（最多 2 轮）

用户确认后，`/idea` 结束。可直接开始执行开发。
