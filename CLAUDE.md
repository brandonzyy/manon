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
