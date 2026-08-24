<div align="center">

# Manon

### 代码库的 AI 架构师

**知识图谱引擎 + 开发技能体系 — 从需求到上线，每一步都基于代码事实。**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-6366f1)](https://modelcontextprotocol.io)
[![License: BSL-1.1](https://img.shields.io/badge/license-BSL--1.1-orange)](LICENSE)

[快速开始](#-快速开始) · [技能体系](#-技能体系) · [知识图谱](#-知识图谱) · [查询工具](#-查询工具) · [MCP 工具](#-mcp-工具)

[English](README.md)

</div>

---

## ❓ 问题

AI 编程有两个结构性缺陷：

| 缺陷 | 表现 | 后果 |
|------|------|------|
| **上下文不足** | 模型看不到调用图、依赖链、模块边界 | **幻觉** — 猜关系、漏副作用 |
| **流程无结构** | 模型不做需求分析就直接写代码 | **失控** — 范围蔓延、没测试、静默回归 |

这两个缺陷会在三个层面产生 19 种具体失败模式：

| 层面 | 问题 | 典型表现 |
|------|------|---------|
| **架构层** | 在看不到全局的情况下做结构决策 | 不必要的抽象层、过度模块化、过早泛化、配置/事件系统过度设计 |
| **模块层** | 在不了解依赖关系的情况下划分边界 | 单模块功能膨胀、职责不清、跨模块重复、耦合过重 |
| **代码层** | 在不知道谁调用谁的情况下改代码 | 死代码残留、引入循环依赖、函数拆得过碎、内聚度低 |

对 AI 编程来说，这些问题比人类开发者更严重：
- **AI 感知不到架构意图** — 它局部优化，写出技术正确但违反系统设计的代码
- **AI 生成速度远快于验证** — 没有图谱验证，坏模式以机器速度扩散
- **每次 AI 会话都是从零开始** — 不记得过去的决策，相同的结构性错误在多次对话中反复出现

模型越强，这两个问题越严重 — 强模型 + 差上下文 + 无流程 = 自信地输出垃圾，而且更快。

## 💡 方案

Manon 提供两层能力：

**第一层 — 知识图谱**（基础设施）
索引每个函数、类、调用关系、导入链和模块边界。向量 + 图混合搜索。模型需要上下文时，精确获取相关代码 — 不多不少。

**第二层 — 开发技能**（工作流）
六个技能覆盖完整开发生命周期 — 需求、代码质量、测试、体检和验证。每个技能都依赖图谱，确保决策基于代码事实而非 LLM 想象。

```
  /idea        写代码         /dao          /tc          /audit         /exp
  ┌─────┐      ┌─────┐       ┌─────┐       ┌─────┐       ┌─────┐       ┌─────┐
  │需求  │ ──▶ │开发  │  ──▶  │维护  │  ──▶  │测试  │  ──▶  │体检  │  ──▶  │验证  │
  │精化  │     │     │       │简化  │       │覆盖  │       │行为  │       │ E2E │
  └──┬──┘      └──┬──┘       └──┬──┘       └──┬──┘       └──┬──┘       └──┬──┘
     │            │              │              │              │              │
     └───────────  全部基于知识图谱  ─────────────────────────────────────────┘
```

---

## ⚡ 快速开始

### 安装（Claude Code / Cursor / Windsurf）

**macOS / Linux**
```bash
git clone https://github.com/brandonzyy/manon.git
cd manon
bash install.sh
```

**Windows**
```cmd
git clone https://github.com/brandonzyy/manon.git
cd manon
install.bat
```

安装器自动检测编辑器、安装依赖、注册免费账户、配置 MCP + Playwright 并安装所有技能。重启编辑器即可使用。

> **首次使用：** 在 Claude Code 中输入 `/manon` 激活。Manon 会索引项目并进入知识图谱模式。

**官方 SaaS** — 免费、零配置、按区域自动路由，无需搭建服务器。

<details>
<summary>环境变量（可选）</summary>

| 变量 | 默认值 | 说明 |
|------|-------|------|
| `MANON_API_URL` | 自动路由 | 覆盖 API 端点。自托管用 `http://localhost:3700` |
| `MANON_API_KEY` | 自动生成 | API 密钥（首次使用时自动创建） |

</details>

<details>
<summary>手动 MCP 配置</summary>

添加到 `~/.claude/settings.json`（Claude Code）或 `~/.cursor/mcp.json`（Cursor）：

```json
{
  "mcpServers": {
    "manon": {
      "command": "python",
      "args": ["/path/to/manon/run_mcp.py"],
      "env": {}
    }
  }
}
```

</details>

---

## 🎯 技能体系

技能只在纯 LLM 对话无法胜任时才存在 — 需要外部工具集成（图谱 API、覆盖率数据、Playwright）、确定性流程或结构化输出。如果 Claude 在普通聊天中就能做好，就不需要做成技能。

| 阶段 | 技能 | 做什么 | 为什么需要技能而不是直接聊？ |
|------|------|-------|--------------------------|
| **需求** | `/idea` | 图谱 + GitHub 调研 → 苏格拉底式追问 → 开发文档 | 问题基于代码事实（fan-in、依赖），不是泛泛而谈 |
| **开发** | Claude + 图谱 | 用 `manon_search` / `manon_graph` 写代码 | Hooks 强制图谱优先；每次 commit 后自动 `manon_impact` |
| **维护** | `/dao` | 健康扫描 → 三层分类 → 自动简化 | 批量架构/模块/代码分析 + 图谱验证 |
| **测试** | `/tc` | 覆盖率扫描 → 图谱排优先级 → 写测试 → 验证 | 按结构重要性排序，不是随机 |
| **体检** | `/audit` | 契约对账划范围 → 五类缺陷谱系语义审计 | 找测试全绿、健康分高时仍然存在的那类缺陷 |
| **验证** | `/exp` | AI Agent 像真实用户一样操作产品 | Playwright/Bash 真实点击、输入、读日志 — 不是想象 |

### `/idea` — 需求精化

查询知识图谱和 GitHub，然后基于发现进行苏格拉底式追问 — "模块 X 的 fan-in 很高，新功能放这里还是新建模块？" 经过 3-7 轮追问后，提出 2-3 个技术方案 + 影响分析，输出可审阅的开发文档。

```
/idea   — 上下文 → 追问 → 方案 → 文档 → 审查
```

### `/dao` — 代码简化

扫描代码健康度，将复杂度分为三层（架构 / 模块 / 代码），A/M 层展示面板让你选择，C 层全部自动修复 + 图谱验证（如死代码删除前先确认零调用者）。

```
/dao    — 健康扫描 → 分类 → A/M 面板 + 自动修 C → 提交
```

### `/tc` — 测试覆盖

扫描覆盖率数据，查询图谱获取实体重要性（fan-in、复杂度、中心性），生成未测代码优先级列表，写测试、跑测试、提交。

```
/tc     — 覆盖率扫描 → 图谱排序 → 写测试 → 验证 → 提交
```

### `/audit` — 行为层体检

结构分回答「形状对不对」，测试回答「正路走得通」。两样同时为绿，仍然可以有一批缺陷活着 —— 它们全在**负向路径**上。

先跑四张契约对账表（零模型、秒级、可进 CI），把范围收窄到有嫌疑的面，再按五类缺陷谱系做语义审计。

```
/audit  — 对账表划范围 → 五类谱系并行审计 → 负向用例作完成判据 → 落棘轮
```

| 表 | 对的是什么账 |
|---|---|
| endpoints | 后端声明的路由 ↔ 任何人调用的 URL（跨语言，靠字符串连的边） |
| configs | 声明的旋钮 ↔ 真正读它的代码（诱饵旋钮 / 只向下游传播的死变量） |
| states | schema 允许的状态值 ↔ 代码写的和读的（死状态 / 幻想状态） |
| envelope | 路由入口 → 敏感汇点，中间有没有经过门禁 |

也可以脱离模型单独跑，用于 CI 和 git hook：

```bash
python scripts/manon-contract-audit.py <项目路径> --fail-on new --baseline <repo_id>
```

`--fail-on new` 只在**新增**死面时失败，所以接入当天不会把所有人的 push 都挡住。

### `/exp` — 体验验证

AI Agent 像真实用户一样操作产品。支持 4 种产品类型：

| 类型 | 工具 | 适用场景 |
|------|------|---------|
| `web` | Playwright MCP | 前端页面 |
| `cli` | Bash | 脚本、命令行工具 |
| `service` | curl + 日志 | 后端 API |
| `hybrid` | Playwright + Bash | 全栈 |

```
/exp    — 定义场景 → Agent 操作 → 报告 → 修复 → 重测（最多 3 轮）
```

---

## 🔬 知识图谱

### 架构

```
┌─ 本地端 (manon_mcp) ────────────────┐     ┌─ 云端 (saas) ──────────────────────┐
│                                      │     │                                      │
│  IDE (Claude Code / Cursor / ...)    │     │  FastAPI 应用 (saas/main.py)         │
│       ↕ MCP 协议                     │     │       ↕                              │
│  manon_mcp/server.py                 │     │  路由层                               │
│    ├─ tools/   (MCP 工具处理)         │     │    query / indexing / repos / ...    │
│    ├─ _client  (HTTP → SaaS API)     │     │       ↕                              │
│    ├─ _sync    (扫描 + 批量上传)      │     │  MatrixoneGraph（门面）               │
│    └─ _hooks   (git + 编辑器钩子)     │     │    ├─ CodeGraph  (NetworkX 有向图)    │
│       ↕                              │     │    ├─ VectorIndex (numpy 余弦相似度)  │
│  core/ast (tree-sitter AST 解析)     │     │    ├─ pipeline   (AST → 图谱)        │
│  codeindex/ (各语言解析器)            │     │    └─ impact     (commit 影响分析)    │
│                                      │     │       ↕                              │
│  ① 扫描文件                           │     │  services/                           │
│  ② 本地 AST 解析                      │     │    llm.py (deep_query 深度查询)      │
│  ③ 上传变更文件 ─────────────────────┼────▶│    embedding (向量生成)               │
│                                      │     │                                      │
│  ⑤ 查询结果 ◀───────────────────────┼─────┤  ④ 构建图谱 + 向量                    │
└──────────────────────────────────────┘     └──────────────────────────────────────┘
```

- **代码留在本地** — 只上传 AST 数据，不需要推送到 Git
- **增量同步** — 文件哈希检测变更，只上传差异部分
- **混合检索** — 图遍历（结构精确）+ 向量搜索（语义模糊）

### 代码健康度（8 维度）

| 缩写 | 维度 | 衡量内容 |
|------|------|---------|
| MC | 模块耦合度 | 跨模块依赖比例 |
| CD | 循环依赖 | 循环数量 |
| FI | 扇入集中度 | 高扇入实体比例 |
| DC | 死代码 | 零调用者实体比例 |
| FS | 函数复杂度 | 超大函数比例 |
| TD | 技术债务 | TODO/FIXME 密度 |
| MF | 模块碎片化 | 微型模块 + 深路径比例 |
| RE | 间接层密度 | 桶式重导出比例 |

### 语言支持

**专用解析器**（深度提取 — 符号、调用、导入、继承、路由）：
Python, TypeScript, JavaScript, Java, PHP

**通用解析器**（符号 + 导入，tree-sitter 自动下载）：
Go, Rust, C, C++, C#, Ruby, Swift, Kotlin, Scala, Lua, R, Elixir, Dart, Haskell, OCaml, Bash, Zig

---

## 📊 效果验证

### 1. 查询智能

图谱查询相比原生工具（Grep/Glob/Read）的提升：

**实际项目基准测试** — OpenClaw 项目，2,100 文件。完整报告：[`docs/MANON_VS_NATIVE_COMPARISON_EN.md`](docs/MANON_VS_NATIVE_COMPARISON_EN.md)

| 维度 | Manon | 原生工具 | 差异 |
|------|-------|---------|------|
| **耗时** | ~30 分钟 | ~8-12 小时 | **快 16-24 倍** |
| **准确率** | 95%+ | 60-70% | **+30%** |

**查询工具基准测试** — 20 个真实查询。完整报告：[`docs/manon-query-tools-evaluation-en.md`](docs/manon-query-tools-evaluation-en.md)

| 指标 | Manon | 原生工具 | 改进 |
|------|-------|---------|------|
| 每任务工具调用 | 1 | 13.7 | **减少 91%** |
| 总 Token 数 | ~19.5K | ~350K | **节省 94%** |
| 质量评分 | 4.3/5 | 3.2/5 | **+34%** |

### 2. 开发生命周期（自举验证）

Manon 用自己的技能开发自己。以下是真实产出数据，不是合成测试。

**`/dao` — 代码简化**

应用于 Manon 自身代码库（93 文件，800+ 实体）：

| 指标 | 之前 | 之后 | 变化 |
|------|------|------|------|
| 代码健康度评分 | 88/100 | 97/100 | **+9** |
| 死代码实体 | 47 | 29 | **-38%** |
| 测试覆盖率 | 32% | 61% | **+29pp** |
| 跨模块关系 | 0 | 48 | 从零修复（图谱 bug） |

`/dao` 识别并自动修复了：死函数、过度碎片化的模块、桶式重导出、循环依赖，并合并了 4 个冗余文件 — 全部在提交前通过图谱验证。

**`/exp` — 体验验证**

用 `/exp` 在发布前测试 `/idea` 技能：

| 轮次 | 结果 | 发现的 Bug | 修复 |
|------|------|-----------|------|
| 第 1 轮 | 4/5 通过 | 图谱 API 响应解析错误；Windows GBK 编码崩溃 | 修复关系匹配 + UTF-8 输出 |
| 第 2 轮 | 0/1 通过 | 符号过滤器过严；中文查询崩溃 | 放宽过滤 + 编码修复 |
| 第 3 轮 | 1/1 通过 | — | 全部场景通过 |

3 轮测试，发现 4 个真实 Bug — 没有 `/exp` 这些 Bug 会直接发布给用户。

**`/idea` — 需求精化**

完整流程测试（批量仓库导入功能）：
- Phase 1（上下文）：一次脚本调用获取 15 个相关模块 + 3 个图谱条目 + 健康评分
- Phase 2（追问）：生成 5 个苏格拉底式问题，全部基于图谱事实（fan-in、模块边界）
- Phase 3（方案）：3 个技术方案，每个带影响分析
- Phase 4（文档）：生成完整开发文档，通过 5 维度自动审查

### 3. 自增强循环

技能之间互相增强：`/idea` 定义需求 → 基于图谱上下文写代码 → `/dao` 清理复杂度 → `/tc` 补测试 → `/exp` 端到端验证 → 发现的问题进入下一轮循环。Manon v1.0→v1.2.4 完全通过这个循环开发。

---

## 🔍 查询工具

### `manon_search` — 语义代码搜索

将自然语言转为向量嵌入，检索最相关实体，沿图谱边展开。解决"不知道关键词"的问题。

### `manon_graph` — 调用图遍历

方向性遍历（调用者/被调用者/双向），可配置深度。解决"改这个函数会不会影响别的"问题。

### `manon_deep_query` — 多轮深度查询

服务端 LLM 迭代查询。自动识别信息缺口并补充。一次调用覆盖跨模块架构问题。

### `manon_impact` — Commit 影响分析

解析 diff → 提取变更符号 → 沿调用边追踪 2 跳 → 计算风险评分（0-100）。即时 CI/CD 门控。

<details>
<summary>工具选择指南</summary>

```
你需要什么？
├── 找代码（不知道关键词）    → manon_search
├── 找代码（知道关键词）      → Grep
├── 追踪调用关系             → manon_graph
├── 跨模块架构理解           → manon_deep_query
├── 评估 commit 风险         → manon_impact
├── 修改代码前               → manon_search + manon_graph
└── 简单文件查找             → Glob
```

</details>

---

## 🛠️ MCP 工具

| 类别 | 工具 | 说明 |
|------|------|------|
| **仓库** | `manon_init` | 自动检测并注册项目 |
| | `manon_repos_list` | 列出仓库和索引状态 |
| | `manon_repos_create/get/delete` | 增删查操作 |
| **索引** | `manon_index_status` | 查看索引进度 |
| | `manon_push_update` | 增量同步 |
| **查询** | `manon_search` | 语义代码搜索 |
| | `manon_graph` | 调用图遍历 |
| | `manon_impact` | Commit 影响分析 |
| | `manon_deep_query` | 多轮深度查询 |
| | `manon_code_health` | 8 维度健康评分 |
| **自动化** | `manon_setup_hooks` | 安装 git pre-push 钩子 |
| **工具** | `manon_config/account/usage` | 配置和账户信息 |

### 自动化（Hooks）

| 钩子 | 触发时机 | 作用 |
|------|---------|------|
| **git pre-push** | `git push` 后 | 自动更新图谱 + 输出健康度变化 |
| **PreToolUse** | Grep/Glob/Agent 前 | 提醒先查图谱 |
| **PostToolUse** | `git commit` 后 | 触发 `manon_impact` 分析 |

---

## ⚙️ 配置

所有配置存储在 `~/.manon/config.json`，自动创建。

| 配置项 | 默认值 | 说明 |
|--------|-------|------|
| `api_key` | 自动生成 | 免费密钥 |
| `api_url` | 按区域路由 | 服务端点 |
| `projects` | `{}` | 本地项目注册表 |

通过 `MANON_API_KEY`、`MANON_API_URL` 环境变量覆盖。

> **自托管：** 设置 `MANON_API_URL=http://localhost:3700`。参见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。

---

## 📋 版本历史

| 版本 | 日期 | 摘要 |
|------|------|------|
| **v1.2.4** | 2026-03-22 | `/idea` + `/exp` 技能；HANDLES 边类型；Playwright MCP 自动配置；完整技能生态 |
| **v1.2.3** | 2026-03-22 | `/tc` 技能；健康维度 MF/RE；`_resolve()` repo_id 容错；dao 代码简化；发布工具 |
| **v1.2.2** | 2026-03-21 | Bug 修复：install.sh 崩溃、Windows MANON_DIR、幻影节点；TS/JS 覆盖率；扫描加速 |
| **v1.2.1** | 2026-03-20 | 知识图谱质量升级：幻影节点修复、跨模块边恢复、类型推断；关系 +74%；健康度 97/100 |
| **v1.2.0** | 2026-03-19 | 脚本分类器；LLM 分类端点；+115 测试；健康度 94/100 |
| **v1.1.2** | 2026-03-19 | 通过 `/dao` 大规模清理：死代码移除，测试覆盖率 32%→61% |
| **v1.1.0** | 2026-03-18 | `/dao` 技能集成；MCP 工具整合 |
| **v1.0.0** | 2026-03-16 | 架构简化；完整测试套件 |
| **v0.2.0** | 2026-02-23 | 首个开源版本 |

---

## 📋 环境要求

- Python 3.10+（Windows 上通过 `winget` 自动安装）
- MCP 客户端：Claude Code、Cursor、Windsurf、Zed、Continue 或 CodeBuddy
- 网络连接

## 🏗️ 自托管

参见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)：Ollama 本地部署、OpenAI 兼容 LLM 配置、多用户设置。

## 🤝 贡献

参见 [`CONTRIBUTING.md`](CONTRIBUTING.md) 了解开发环境设置和贡献指南。

## 💬 社区与支持

- **Issues**：[报告 Bug 或提交需求](https://github.com/brandonzyy/manon/issues)
- **Discussions**：[提问或分享想法](https://github.com/brandonzyy/manon/discussions)

## 📄 许可证

MIT 协议 — 详见 [LICENSE](LICENSE)。

Copyright (c) 2026 一码行云（杭州）信息科技有限公司
