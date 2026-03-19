<div align="center">

# Manon（马浓）

### AI 编程的上下文管理系统

**基于自研 MatrixOneGraph 知识图谱引擎的 MCP 服务，让 AI 编程更精准、更可控。**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-6366f1)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)

[快速开始](#-快速开始) · [工作原理](#-工作原理) · [查询工具详解](#-查询工具详解) · [MCP 工具](#-mcp-工具一览) · [API 参考](#-api-参考)

</div>

---

## ❓ 问题

AI 编程的核心缺陷：**上下文不足**。

| 缺陷 | 表现 | 后果 |
|------|------|------|
| **上下文不足** | 模型看不到调用关系、依赖链、模块边界 | **幻觉** — 猜测关系、遗漏副作用、改一处坏一片 |

模型越强，上下文问题越严重 — 强模型 + 差上下文 = 更快地产出自信的垃圾。

## 💡 解决方案

Manon 是基于自研 **MatrixOneGraph 知识图谱引擎**的 MCP 服务，为 AI 编程提供精准上下文：

**MatrixOneGraph 知识图谱** — 索引代码库中每个函数、类、调用关系、导入链和模块边界。模型需要上下文时，精准获取相关实体和代码 — 不多不少。

- **实体、调用、导入** — 全量索引代码结构
- **向量 + 图谱混合搜索** — 精确关系 + 语义查询
- **精准、最小充分的上下文** — 消除幻觉

## 📊 实测效果

### 真实项目分析基准测试

分析 OpenClaw 项目（2,100 文件）并制定精简方案。完整报告：[`docs/MANON_VS_NATIVE_COMPARISON.md`](docs/MANON_VS_NATIVE_COMPARISON.md)

| 维度 | 使用 Manon | 使用原生工具 | 差异 |
|------|-----------|-------------|------|
| **所需时间** | ~30 分钟 | ~8-12 小时 | **快 16-24 倍** |
| **分析深度** | 深度语义理解 | 表面文本匹配 | Manon 更深入 |
| **准确性** | 95%+ | 60-70% | **+30%** |
| **可信度** | 基于图谱关系 | 基于推测 | Manon 更可靠 |

**核心优势**：
- **语义理解** — 理解代码含义和关系，不只是文本匹配
- **关系图谱** — 52,701 实体、73,865 关系，秒级多层依赖追踪
- **自然语言查询** — 描述意图即可，无需知道精确关键词

### 查询工具评估

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

### 独特价值（原生工具难以实现）

1. **语义搜索** — 用自然语言描述意图，不需要知道代码中的具体命名。搜"错误处理机制"就能找到所有异常相关代码，而不是只匹配 `Exception` 关键词
2. **方向性图遍历** — 区分 callers（谁调用了它）vs callees（它调用了谁），原生 Grep 只能找到引用行，无法区分调用方向
3. **自动覆盖度分析** — LLM 自动判断信息缺口并补充查询，跨模块复杂问题一次搞定，纯工具无法实现
4. **结构化实体+关系** — 返回的不是文本行，而是带类型、评分、关系的结构化数据
5. **秒级影响筛查** — 1 次调用获得变更符号、调用者、传播链、风险评分的完整报告，可直接用于 CI/CD 门禁

### 工具选择决策树

```
需求是什么？
├── 找代码/找功能（不确定关键词）
│   └── manon_search → 不足时 Grep 补充
├── 找代码（知道精确关键词）
│   └── Grep（更快更精确）
├── 查调用关系/依赖
│   └── manon_graph → depth=1 不够时加深
├── 理解跨模块架构
│   └── manon_deep_query（自动多轮）
├── 评估 commit 影响
│   ├── 快速筛查 → manon_impact
│   └── risk ≥ 60 → manon_impact + 原生深度分析
├── 修改代码前
│   └── manon_search + manon_graph（了解上下文）
└── 简单文件查找
    └── Glob
```

## ⚡ 开箱即用

### 使用官方服务（推荐）

Manon 提供免费的官方 SaaS 服务 — 无需自建服务器。安装后 MCP 客户端自动连接官方 API（按地区自动路由）。

**环境变量**（可选，用于自定义）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MANON_API_URL` | 自动（按地区路由） | 覆盖 API 地址。自部署设为 `http://localhost:3700` |
| `MANON_API_KEY` | 自动生成 | API 密钥（首次使用自动创建） |
| `MANON_API_URL_CN` | `http://saas.matrixone.online:3700` | 国内节点 |
| `MANON_API_URL_INTL` | `http://203.208.134.27:3700` | 国际节点（新加坡） |

使用官方服务只需安装运行，无需设置任何环境变量。

### 安装（Claude Code / Cursor / Windsurf）

**macOS / Linux**
```bash
# GitHub（推荐）
git clone https://github.com/brandonzyy/manon.git
# 或使用 Gitee 镜像（国内更快）
git clone https://gitee.com/ymxy_1_0/manon.git

cd manon
bash install.sh
```

**Windows**
```cmd
# GitHub（推荐）
git clone https://github.com/brandonzyy/manon.git
# 或使用 Gitee 镜像（国内更快）
git clone https://gitee.com/ymxy_1_0/manon.git

cd manon
install.bat
```
安装器自动检测编辑器、安装依赖、注册免费账号并配置 MCP 服务。Windows 下优先使用 Git Bash，回退到 PowerShell — 缺少 Python 时通过 `winget` 自动安装。重启编辑器即可使用。

> **自动包含：** 安装时自动包含 `/dao` skill — 详见下方[大道至简 (Dao)](#-大道至简-dao--图谱驱动的代码精简)章节。
>
> **首次使用：** 在 Claude Code 中输入 `/manon` 激活。Manon 会索引项目并进入知识图谱模式。Cursor/Windsurf 中工具自动可用。

<details>
<summary>手动 MCP 配置</summary>

在编辑器的 MCP 配置中添加（Claude Code: `~/.claude/settings.json`，Cursor: `~/.cursor/mcp.json`）：

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

API Key 在 `~/.manon/config.json` 中自动管理，无需手动设置。

</details>

### 初始化（一次性）

```
安装完成后，在 IDE 中首次使用：

manon_init          → 自动检测项目、注册仓库、构建知识图谱
                      同时安装 Claude Code hooks（搜索前/改代码前自动提醒查图谱）
manon_setup_hooks   → 安装 git pre-push hook，push 后自动更新图谱并输出健康评分
manon_code_health   → 首次代码体检，获取 8 维度健康评分基线
```

三步完成，之后所有工具自动可用。

**Claude Code Hooks（install.sh/install.bat 安装）：**
- **Grep/Glob 前** — 提醒先查知识图谱，避免盲目搜索
- **Agent 前（Explore/general-purpose）** — 提醒在启动探索型 agent 前先查询 Manon
- **Commit 后** — git commit 成功后自动触发 manon_impact 影响分析

**Git Pre-Push Hook（manon_init 安装）：**
- push 后自动增量更新知识图谱
- 自动输出代码健康评分变化
- 也可通过 manon_setup_hooks 手动安装

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

## 🌿 大道至简 (Dao) — 图谱驱动的代码精简

与 Manon 捆绑安装，`/dao` 是一个 Claude Code skill，利用知识图谱系统化发现并消除代码库中不必要的复杂度——不靠猜测，基于图谱数据说话。

在 Claude Code 中输入 `/dao`，它会查询图谱健康评分，定位复杂度集中的位置，并将问题分为三层：

| 层级 | 范围 | 示例 |
|------|------|------|
| **架构层 (A)** | 系统结构 | 不必要的分层、过度模块化、过早泛化 |
| **模块层 (M)** | 模块边界 | 功能臃肿、边界不清、重复、过度依赖 |
| **代码层 (C)** | 文件与函数 | 死代码、过度碎片化、循环依赖、低内聚 |

**架构和模块层问题**通过交互面板展示，由你选择要处理的 issue，Claude 制定方案并经你审批后执行，提交并同步图谱。

**代码层问题**自动循环处理，无需确认。每次修复前验证图谱数据（如删除死代码前确认零调用者），修复后提交并更新图谱，再继续下一个。

Issue 记录在 `.dao/issues.json`，健康评分每次提交后自动更新。

```
/dao    — 查询图谱 → 展示 A/M 面板 → 自动修复所有 C 层问题 → 停止
```

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

---

## 🔍 查询工具详解

Manon 提供 4 个核心查询工具，覆盖从代码搜索到架构分析的完整场景。每个工具基于知识图谱实现，单次 MCP 调用即可完成原生工具需要 7-20 次调用才能完成的任务。

### `manon_search` — 语义代码搜索

**原理：** 将自然语言查询转换为向量嵌入，在知识图谱的向量索引中检索语义最相近的实体，同时沿图谱边扩展关联实体和关系，返回实体 + 关系 + 代码片段的一体化结果。

**目标：** 解决"不知道搜什么关键词"的问题。用户描述意图（如"错误处理机制"），即可找到所有相关代码，不受命名风格限制。

| 维度 | 说明 |
|------|------|
| 输入 | 自然语言查询 + top_k + depth |
| 输出 | 匹配实体（带相关度评分）+ 关系边 + 代码片段 |
| 核心优势 | 语义理解 > 关键词匹配；跨模块聚合；不需要知道具体命名 |
| 最佳场景 | 探索性搜索、概念性查询、新人 onboarding |
| 局限性 | 非常具体的字符串搜索不如 Grep 精确 |

### `manon_graph` — 调用图遍历

**原理：** 在知识图谱中定位目标 symbol，沿调用边进行方向性遍历（callers = 谁调用了它，callees = 它调用了谁），支持多层深度展开，返回完整的结构化调用链。

**目标：** 解决"改这个函数会影响哪里"的问题。一次调用看清 symbol 在系统中的所有使用场景和依赖关系，原生 Grep 只能找到引用行，无法区分调用方向。

| 维度 | 说明 |
|------|------|
| 输入 | symbol 名称 + direction (callers/callees/both) + depth |
| 输出 | 调用者/被调用者列表 + 调用链路径 + 实体详情 |
| 核心优势 | 方向性遍历；多层深度；结构化调用链 |
| 最佳场景 | 修改前影响评估、理解模块间依赖、追踪数据流 |
| 局限性 | 动态调用（反射、eval）可能遗漏 |

### `manon_deep_query` — 多轮深度查询

**原理：** 服务端 LLM 驱动的多轮迭代查询。LLM 分析已有上下文的覆盖度，自动识别信息缺口，生成补充查询，直到判断所有子方面都已覆盖。单次 MCP 调用，服务端完成所有迭代。

**目标：** 解决"跨模块复杂问题需要多轮探索"的问题。用户提出一个架构级问题，系统自动拆解、逐项查询、综合回答 — 无需人工引导多轮搜索。

| 维度 | 说明 |
|------|------|
| 输入 | 自然语言问题 + max_rounds |
| 输出 | 综合分析报告（覆盖所有子方面）+ 每轮查询记录 |
| 核心优势 | 自动识别覆盖缺口 + 自动补充查询；跨模块一次搞定 |
| 最佳场景 | 跨模块架构理解、多子系统关联分析、新人 onboarding |
| 局限性 | 复杂元查询可能超时降级为单轮 |

### `manon_impact` — 提交影响分析

**原理：** 解析 commit 的 diff，提取变更符号（函数/类），在知识图谱中沿调用边反向追踪 2 跳，识别所有直接和间接调用者，计算受影响模块和传播链路，输出量化风险评分（0-100）。

**目标：** 解决"这次提交会不会搞坏别的地方"的问题。秒级获得完整的影响报告，可直接用于 CI/CD 门禁判断。高风险提交（≥60 分）建议配合人工深度审查。

| 维度 | 说明 |
|------|------|
| 输入 | commit hash + max_depth |
| 输出 | 变更符号 + 调用者追踪 + 受影响模块 + 传播链 + 风险评分 |
| 核心优势 | 秒级风险筛查；量化评分；传播链可视化；自动识别受影响测试 |
| 最佳场景 | 快速风险筛查、CI/CD 门禁、code review 辅助 |
| 局限性 | 2 跳深度限制截断远端影响；无法识别语义级行为变更 |

---

## 🛠️ MCP 工具一览

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

> **官方服务 vs 自部署：** 默认连接官方 SaaS 服务（按地区自动路由）。如需使用自部署服务器，设置 `MANON_API_URL=http://localhost:3700`。自部署详见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。

---

## 📋 版本历史

| 版本 | 日期 | 摘要 |
|------|------|------|
| **v1.2.0** | 2026-03-19 | 脚本分类器过滤工具脚本；LLM 分类端点；新增 115 个单元测试；代码健康 94/100 |
| **v1.1.2** | 2026-03-19 | `/dao` 大规模代码清理：死代码删除、函数拆分、测试覆盖率 32%→61%；TC 维度支持真实覆盖率数据 |
| **v1.1.1** | 2026-03-18 | 修复索引覆盖率统计不准确 |
| **v1.1.0** | 2026-03-18 | 捆绑 `/dao` skill；C2/C4/C8 代码精简；MCP 工具合并 |
| **v1.0.0** | 2026-03-16 | 架构精简：`mcp/`→`manon_mcp/`，移除 `shared/`；完整测试套件 |
| **v0.2.5** | 2026-03-13 | 扫描/上传分离避免 MCP 超时；重量级操作移出 MCP 进程；Claude Code hooks |
| **v0.2.2** | 2026-03-07 | codeindex 内嵌入仓库，消除外部依赖；快速语言检测 |
| **v0.2.1** | 2026-03-07 | 迁移至 brandonzyy/codeindex；自动安装 tree-sitter 解析器 |
| **v0.2.0** | 2026-02-23 | 首次开源发布，MCP 集成 + 知识图谱 |

---

## 📦 更新日志

### v1.2.0 — 2026-03-19

**`/dao` Hook 强制机制** — 两段式 Hook（EnterPlanMode 标记 + Stop 阻断）确保计划执行后 `dao-commit` 必然运行，issue 关闭并同步图谱。

- **脚本分类器** — 通过四级规则链将工具脚本（`deploy_*`、`setup_*` 等）过滤出索引，不确定的文件送 LLM 兜底。
- **`POST /api/v1/classify-scripts`** — 供扫描器调用的 LLM 脚本分类新端点。
- **修复** — 灰度测试中发现的 3 个分类器 Bug：导入字典键名错误、相对导入未解析、`build_imported_paths` 键名错误。
- **重构** — `git_parser.py` + `symbol_extractor.py` 合并为 `parsing.py`；测试框架检测提取至 `framework_detection.py`。
- **测试** — 新增 115 个单元测试（脚本分类器 + 分类端点）。
- **代码健康** — `94/100`，较上版提升 6 分。

---

## 🗺️ 路线图

### 结构化流水线（规划中）

AI 编程的另一个结构性缺陷是**执行无结构** — 拿到需求直接写代码，导致注意力衰减、需求漂移、架构崩塌。

我们正在开发结构化流水线，强制执行确定性工作流：`澄清 → 规格 → 设计 → 分解 → 执行 → 审查`。每步有界、输入输出明确、结果可见可干预。结合知识图谱提供的精准上下文，从根本上消除 AI 编程的黑盒问题。

---

## 📋 系统要求

- Python 3.10+（Windows 下缺少时通过 `winget` 自动安装）
- MCP 使用：Claude Code、Cursor、Windsurf、Zed、Continue 或 CodeBuddy
- 网络连接

## 🏗️ 自托管部署

想要运行自己的 Manon 服务器？查看 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) 了解：
- 使用 Ollama 本地部署
- OpenAI 兼容的 LLM 配置
- 多用户设置
- Docker 部署（即将推出）

## 🤝 参与贡献

Manon 是开源项目，欢迎贡献！查看 [`CONTRIBUTING.md`](CONTRIBUTING.md) 了解：
- 开发环境设置
- 代码风格指南
- Pull Request 流程
- 可贡献的领域

## 💬 社区与支持

- **问题反馈**：[报告 Bug 或请求功能](https://github.com/brandonzyy/manon/issues)
- **讨论交流**：[提问或分享想法](https://github.com/brandonzyy/manon/discussions)
- **文档**：[`docs/`](docs/) 查看架构和部署指南

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE)

Copyright (c) 2026 一码行云（杭州）信息科技有限公司
