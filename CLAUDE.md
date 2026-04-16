# Manon — CLAUDE.md

## 项目概述

Manon (马浓) 是 AI 架构师工具，FastAPI 后端 + 单页 HTML 前端 + WebSocket 实时通信。

## 项目结构

```
web/       — Web 客户端 (FastAPI + 前端 + coach + worker, :3600)
manon_mcp/ — MCP 服务端 (IDE 集成, Claude Code)
core/      — 核心模块 (saas_client, ast_sync)
saas/      — 数据服务后端 (:3700)
```

## R760 生产服务器

| 项目 | 值 |
|------|-----|
| 地址 | `114.94.190.2:2212` |
| 用户 | `root` |
| 认证 | key-based — `~/.ssh/id_ed25519` |
| 服务目录 | `/root/manon` |
| 服务端口 | `:3700` |

```bash
# SSH 登录
ssh -i ~/.ssh/id_ed25519 -p 2212 root@114.94.190.2

# 部署（强制）
python scripts/deploy-r760.py

# 部署（仅服务端文件变更时）
python scripts/deploy-r760.py --auto
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

## /experience 体验驱动验证

开发完成后，执行 `/experience` 让 AI 像真实用户一样操作产品验证功能：

```
/experience
> 测试 Manon Web 的基础聊天功能
> 测试 manon-scan.py 的增量扫描
> 测试 SaaS API 的 /search 端点
```

流程：DEFINE（生成 spec）→ EXPERIENCE（Agent 操作产品）→ FIX（修复问题）→ RE-EXPERIENCE（重测，最多 3 轮）

支持 4 种产品类型：
| type | 工具 | 适用场景 |
|------|------|---------|
| `web` | Playwright MCP | 前端页面 |
| `cli` | Bash | 命令行工具、脚本 |
| `service` | Bash (curl + 日志) | 后端 API、服务 |
| `hybrid` | Playwright + Bash | 前后端联动 |

## 页面关键选择器

- Header: `#projectSelect`, `#modelIndicator`, `#wsDot`, `.settings-btn`
- 状态栏: `#statEntities`, `#statRelations`, `#statFiles`, `#statChunks`, `#statGateway`, `#statWorkers`
- 聊天: `#messages`, `#input`, `.msg.user`, `.msg.manon`, `#thinking`
- Pipeline: `#pipelineBanner`, `#pipelineSteps`, `.ps.active`, `.ps.done`
- 模态框: `#setupModal`, `#settingsModal`, `#welcomeOverlay`
