# Manon — CLAUDE.md

## 项目概述

Manon (马浓) 是 AI 架构师工具，FastAPI 后端 + 单页 HTML 前端 + WebSocket 实时通信。

## 项目结构

```
web/       — Web 客户端 (FastAPI + 前端 + coach + worker, :3600)
mcp/       — MCP 服务端 (IDE 集成, Claude Code)
shared/    — 共享模块 (saas_client, ast_sync)
saas/      — 数据服务后端 (:3700)
```

## 浏览器验证（改 web/ 后 MUST）

```bash
node scripts/manon-test-base.mjs --live '<操作序列 JSON>'
```
| 改动类型 | 操作序列 |
|---------|---------|
| HTML/CSS | `[{"action":"inspect"}]` |
| 聊天 | `[{"action":"send-chat","text":"你好"},{"action":"wait-response","timeout":30000}]` |
| Pipeline | `[{"action":"send-chat","text":"分析项目"},{"action":"wait-pipeline","state":"clarifying","timeout":15000}]` |
| 模态框 | `[{"action":"click","selector":".settings-btn"},{"action":"wait","selector":"#settingsModal"}]` |

无报错 → 告知用户检查浏览器；有错 → 修复后重跑。等用户反馈再改。

## 页面关键选择器

- Header: `#projectSelect`, `#modelIndicator`, `#wsDot`, `.settings-btn`
- 状态栏: `#statEntities`, `#statRelations`, `#statFiles`, `#statChunks`, `#statGateway`, `#statWorkers`
- 聊天: `#messages`, `#input`, `.msg.user`, `.msg.manon`, `#thinking`
- Pipeline: `#pipelineBanner`, `#pipelineSteps`, `.ps.active`, `.ps.done`
- 模态框: `#setupModal`, `#settingsModal`, `#welcomeOverlay`
