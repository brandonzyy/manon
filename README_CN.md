<div align="center">

# Manon（马浓）

### AI 编程的上下文管理系统

**基于自研 MatrixOneGraph 知识图谱引擎的 MCP 服务，让 AI 编程更精准、更可控。**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-6366f1)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)

[快速开始](#-快速开始) · [工作原理](#-工作原理) · [MCP 工具](#-mcp-工具) · [API 参考](#-api-参考)

</div>

---

## 问题

AI 编程的核心缺陷：**上下文不足**。

| 缺陷 | 表现 | 后果 |
|------|------|------|
| **上下文不足** | 模型看不到调用关系、依赖链、模块边界 | **幻觉** — 猜测关系、遗漏副作用、改一处坏一片 |

模型越强，上下文问题越严重 — 强模型 + 差上下文 = 更快地产出自信的垃圾。

## 解决方案

Manon 是基于自研 **MatrixOneGraph 知识图谱引擎**的 MCP 服务，为 AI 编程提供精准上下文：

**MatrixOneGraph 知识图谱** — 索引代码库中每个函数、类、调用关系、导入链和模块边界。模型需要上下文时，精准获取相关实体和代码 — 不多不少。

- **实体、调用、导入** — 全量索引代码结构
- **向量 + 图谱混合搜索** — 精确关系 + 语义查询
- **精准、最小充分的上下文** — 消除幻觉

## 📊 实测效果

基于 20 个真实查询样本（每工具 5 个），与原生工具（Grep/Glob/Read/git）在相同任务上对比。完整报告：[`docs/manon-query-tools-evaluation.md`](docs/manon-query-tools-evaluation.md)

| 指标 | Manon | 原生工具 | 提升 |
|------|-------|---------|------|
| 平均工具调用次数 | 1 次 | 13.7 次 | **减少 91%** |
| Token 总消耗（20 次查询） | ~19.5K | ~350K | **节省 94%** |
| 平均质量评分 | 4.3/5 | 3.2/5 | **+34%** |

| 工具 | 场景 | 调用节省 | 质量（Manon → 原生） |
|------|------|---------|---------------------|
| `manon_search` | 语义代码搜索 | 86% | 4.2 vs 2.6 |
| `manon_graph` | 调用图遍历 | 90% | 4.6 vs 2.6 |
| `manon_deep_query` | 多轮架构分析 | 94% | 4.6 vs 2.6 |
| `manon_impact` | 提交影响分析 | 95% | 3.8 vs 4.8 ¹ |

> ¹ `impact` 以深度换速度 — 用 1/66 的 token 获取 80% 的洞察。高风险提交建议配合人工审查。

## ⚡ 快速开始

### MCP 安装（Claude Code / Cursor / Windsurf）

**macOS / Linux**
```bash
git clone https://gitee.com/ymxy_1_0/manon.git
cd manon
bash install.sh
```

**Windows**
```cmd
git clone https://gitee.com/ymxy_1_0/manon.git
cd manon
install.bat
```
安装器自动检测编辑器、安装依赖、注册免费账号并配置 MCP 服务。Windows 下优先使用 Git Bash，回退到 PowerShell — 缺少 Python 时通过 `winget` 自动安装。重启编辑器即可使用。

> **首次使用：** 在 Claude Code 中输入 `/manon` 激活。Manon 会索引项目并进入知识图谱模式。Cursor/Windsurf 中工具自动可用。

<details>
<summary>手动 MCP 配置</summary>

在编辑器的 MCP 配置中添加（Claude Code: `~/.claude/settings.json`，Cursor: `~/.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "manon": {
      "command": "python",
      "args": ["/path/to/manon/mcp/server.py"],
      "env": {}
    }
  }
}
```

API Key 在 `~/.manon/config.json` 中自动管理，无需手动设置。

</details>

---

## 🔬 工作原理

### MatrixOneGraph 知识图谱（端云架构）

```
本地                                      云端
────                                      ────
① 扫描项目文件
② 本地解析 AST
   (函数、类、调用、导入)
③ 哈希文件，仅发送变更 ──────────────→ ④ 构建知识图谱
                                        ⑤ 生成向量索引
                                        ⑥ 存储实体和关系
                                            ↓
⑧ AI 获取精准上下文 ←────────────────── ⑦ 响应查询
```

- **本地解析，云端存储** — 代码无需推送到 Git 仓库
- **增量同步** — 仅上传变更文件
- **混合搜索** — 图遍历获取精确关系 + 向量搜索获取语义查询

## 🚀 开箱即用

### 初始化（一次性）

```
安装完成后，在 IDE 中首次使用：

manon_init          → 自动检测项目、注册仓库、构建知识图谱
                      同时安装 Claude Code hooks（搜索前/改代码前自动提醒查图谱）
manon_setup_hooks   → 安装 git pre-push hook，push 后自动更新图谱并输出健康评分
manon_code_health   → 首次代码体检，获取 8 维度健康评分基线
```

三步完成，之后所有工具自动可用。

**Claude Code Hooks（manon_init 自动安装）：**
- **Grep/Glob 前** — 提醒先查知识图谱，避免盲目搜索
- **Edit/Write 前** — 提醒先查上下文和近期改动，避免回退已有设计决策

**Git Pre-Push Hook（manon_setup_hooks 安装）：**
- push 后自动增量更新知识图谱
- 自动输出代码健康评分变化

### 日常工作流

```
写代码 → git push → hook 自动更新知识图谱（零操作）
                         ↓
┌─────────────────────────────────────────────────┐
│  查代码      manon_search / manon_graph          │
│  深度分析    manon_deep_query                    │
│  评估改动    manon_impact                        │
│  代码体检    manon_code_health → 8 维度评分      │
│              模块耦合 · 循环依赖 · 扇入集中度    │
│              死代码 · 测试覆盖 · 函数规模        │
│              技术债务 · 继承深度                  │
└─────────────────────────────────────────────────┘
```

> **code_health 评分维度：** 模块耦合度(MC)、循环依赖(CD)、扇入集中度(FI)、死代码(DC)、测试覆盖(TC)、函数规模(FS)、技术债务(TD)、继承深度(ID)。每次 push 后自动输出评分变化。

---

## 🛠️ MCP 工具

### 仓库管理

| 工具 | 说明 |
|------|------|
| `manon_init` | 自动检测并注册当前项目 |
| `manon_repos_list` | 列出所有仓库及索引状态 |
| `manon_repos_create` | 添加仓库（Git URL 或本地路径） |
| `manon_repos_get` | 获取仓库详情 |
| `manon_repos_delete` | 删除仓库 |

### 索引

| 工具 | 说明 |
|------|------|
| `manon_index` | 触发代码索引（构建知识图谱） |
| `manon_index_status` | 查看索引进度 |
| `manon_push_update` | 同步最新变更（增量） |

### 代码智能

| 工具 | 说明 |
|------|------|
| `manon_search` | 语义代码搜索 — 用自然语言找代码 |
| `manon_graph` | 查询调用图和依赖关系 |
| `manon_impact` | 分析最近提交的影响范围 |
| `manon_deep_query` | 多轮深度分析，LLM 推理 |
| `manon_code_health` | 代码健康度评分 — 8 维度分析 |

### 自动化

| 工具 | 说明 |
|------|------|
| `manon_setup_hooks` | 安装 git pre-push hook，push 后自动更新图谱并输出健康评分 |

### 工具

| 工具 | 说明 |
|------|------|
| `manon_config` | 显示当前配置 |
| `manon_account` | 显示账号信息和配额 |
| `manon_usage` | 查看 API 使用统计 |
---

## 📡 API 参考

基础 URL: `http://your-server:3700/api/v1` — 所有端点需要 `X-API-Key` 请求头。

<details>
<summary>仓库</summary>

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/repos` | 创建仓库 |
| `GET` | `/repos` | 列出仓库 |
| `GET` | `/repos/{id}` | 获取仓库 |
| `DELETE` | `/repos/{id}` | 删除仓库 |

</details>

<details>
<summary>索引</summary>

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/repos/{id}/index` | 触发索引 |
| `GET` | `/repos/{id}/index-status` | 查看状态 |
| `POST` | `/repos/{id}/push-update` | 增量更新 |
| `POST` | `/repos/{id}/sync-ast` | 上传本地 AST 数据 |

</details>

<details>
<summary>查询</summary>

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/repos/{id}/search` | 语义搜索 |
| `GET` | `/repos/{id}/graph` | 图遍历 |
| `GET` | `/repos/{id}/impact` | 影响分析 |
| `POST` | `/repos/{id}/deep-query` | 多轮深度查询 |

</details>

<details>
<summary>账号</summary>

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/register` | 自助注册 |
| `GET` | `/account` | 账号信息 |
| `GET` | `/usage` | 使用统计 |

</details>

---

## ⚙️ 配置

所有配置存储在 `~/.manon/config.json`，首次运行自动创建。

| 设置 | 默认值 | 说明 |
|------|--------|------|
| `api_key` | 自动生成 | 免费层 Key，首次使用时获取 |
| `api_url` | 自动路由 | 服务端点（按地区自动选择） |
| `projects` | `{}` | 本地项目注册表和文件哈希 |

可通过环境变量覆盖：`MANON_API_KEY`、`MANON_API_URL`。

---

## 🗺️ 路线图

### 结构化流水线（规划中）

AI 编程的另一个结构性缺陷是**执行无结构** — 拿到需求直接写代码，导致注意力衰减、需求漂移、架构崩塌。

我们正在开发结构化流水线，强制执行确定性工作流：`澄清 → 规格 → 设计 → 分解 → 执行 → 审查`。每步有界、输入输出明确、结果可见可干预。结合知识图谱提供的精准上下文，从根本上消除 AI 编程的黑盒问题。

---

## 系统要求

- Python 3.10+（Windows 下缺少时通过 `winget` 自动安装）
- MCP 使用：Claude Code、Cursor、Windsurf、Zed、Continue 或 CodeBuddy
- 网络连接

## 许可证

MIT
