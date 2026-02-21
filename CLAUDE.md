# Manon — CLAUDE.md

## 项目概述

Manon (马浓) 是 AI 架构师工具，FastAPI 后端 + 单页 HTML 前端 + WebSocket 实时通信。
运行端口: `http://localhost:3600`

## 项目结构

```
web/       — Web 客户端 (FastAPI + 前端 + coach pipeline + worker)
mcp/       — MCP 服务端 (IDE 集成, Claude Code 等)
shared/    — 共享模块 (saas_client, ast_sync)
saas/      — 数据服务后端 (port 3700)
```

## 浏览器验证流程（MUST）

**每次修改 `web/` 下的文件后，必须用 `--live` 模式打开真实浏览器，模拟用户操作流程，让用户亲眼观察验证。**

### 流程

1. **改完代码后**，用 `--live` 在后台启动浏览器验证：
   ```bash
   node scripts/manon-test-base.mjs --live '<操作序列 JSON>'
   ```
   浏览器会打开，逐步执行操作（带 300ms 慢动作），用户可以实时观察。

2. **读取结果 JSON** 检查是否有报错：
   ```bash
   cat web/static/test-results/latest-interact.json
   ```

3. **如果有 console 错误或网络失败**：直接修复代码，然后重新跑 `--live`

4. **如果无报错**：告诉用户
   > 浏览器已打开，操作流程执行完毕，无报错。请在浏览器中检查页面，看看是否符合预期。

5. **等待用户反馈**：用户观察浏览器后会告诉你哪里需要改进

6. **根据反馈修改** → 重新 `--live` 验证 → 循环直到用户满意
### 根据改动类型构造操作序列

| 改动类型 | 操作序列 |
|---------|---------|
| HTML/CSS 布局 | `[{"action":"inspect"}]` — 打开页面让用户看 |
| 聊天功能 | `[{"action":"send-chat","text":"你好"},{"action":"wait-response","timeout":30000}]` |
| Pipeline/Worker | `[{"action":"send-chat","text":"分析项目"},{"action":"wait-pipeline","state":"clarifying","timeout":15000}]` |
| 设置/模态框 | `[{"action":"click","selector":".settings-btn"},{"action":"wait","selector":"#settingsModal"}]` |
| 纯检查 | 不传 JSON，直接 `--live` 打开页面 |

### 注意

- `--live` 模式浏览器不会自动关闭，用户按 Ctrl+C 退出
- 后台运行时用 `run_in_background`，然后读 JSON 检查错误
- 操作带 `slowMo: 300ms`，用户能看清每一步
- 只有明确的技术错误（console error、网络失败）才自己修，其他等用户反馈

## 页面关键选择器

- Header: `#projectSelect`, `#modelIndicator`, `#wsDot`, `#wsLabel`, `.settings-btn`
- 状态栏: `#statEntities`, `#statRelations`, `#statFiles`, `#statChunks`, `#statGateway`, `#statWorkers`
- 聊天: `#messages`, `#input`, `.msg.user`, `.msg.manon`, `#thinking`
- Pipeline: `#pipelineBanner`, `#pipelineSteps`, `.ps.active`, `.ps.done`
- 模态框: `#setupModal`, `#settingsModal`, `#welcomeOverlay`

## Pipeline 方法论（用于 Plan 模式）

当用户要求实现一个功能或做较大改动时，按以下 4 步结构化流程执行。每步先用 `manon_search` / `manon_graph` 获取图谱上下文，再基于上下文推理。

### Step 1: Spec — 需求规格

将用户需求转化为结构化规格。输出 JSON：

```json
{
  "title": "功能标题",
  "scope": "影响范围描述",
  "requirements": [
    {
      "id": "R1",
      "title": "需求标题",
      "priority": "MUST",
      "scenarios": [
        {
          "title": "场景名",
          "condition": "在什么条件或操作下",
          "expected": "预期结果是什么"
        }
      ]
    }
  ]
}
```

规则：
- priority 取值：MUST（必须）| SHOULD（建议）| MAY（可选）
- 每个 requirement 至少一个 scenario，用自然语言描述验收条件
- 不确定的地方先问用户，不要猜

### Step 2: Design — 技术设计

基于 Spec + 图谱上下文，生成技术方案。输出 JSON：

```json
{
  "approach": "技术方案概述",
  "decisions": [
    {"title": "决策标题", "rationale": "理由"}
  ],
  "fileChanges": [
    {"file": "路径", "action": "new|modify", "description": "说明"}
  ]
}
```

规则：
- 用 `manon_search` 查找相关代码，用 `manon_graph` 查依赖关系
- decisions 记录关键技术选型和理由
- fileChanges 列出所有要改的文件，标明新建还是修改

### Step 3: Decompose — 任务拆解

将设计拆分为可独立执行和验证的子任务。输出 JSON 数组：

```json
[
  {
    "id": 1,
    "title": "任务标题",
    "instruction": "详细开发指令",
    "files": ["path/to/file.ts"],
    "criteria": "验收标准",
    "order": 1
  }
]
```

规则：
- 每个任务对应一个可独立验证的工作项，涉及 3-5 个文件
- order 字段表示执行顺序，相同 order 的任务可并行，不同 order 按顺序
- instruction 要足够详细，包含具体的代码修改指导
- criteria 是可验证的验收标准

### Step 4: Execute — 逐任务执行

按 order 顺序执行每个任务：

1. 用 `manon_search` 获取任务相关的图谱上下文
2. 读取 files 中列出的文件，理解现有代码
3. 按 instruction 修改代码
4. 自测：检查修改是否满足 criteria
5. 失败则换思路重试（最多 2 次），仍失败则报告用户

执行完所有任务后，回顾整体改动，确认各任务间没有冲突。
