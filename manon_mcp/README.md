<p align="center">
  <h1 align="center">马侬 Manon</h1>
  <p align="center">AI 架构师 — 让你的 AI 编程助手真正读懂代码</p>
</p>

<p align="center">
  <a href="#快速安装">安装</a> · <a href="#它能做什么">功能</a> · <a href="#支持的编辑器">编辑器</a> · <a href="#工作原理">原理</a>
</p>

---

## 痛点

AI 编程助手很强，但它们有一个致命短板：**不理解你的项目全貌**。

它们只能看到当前打开的文件，不知道函数被谁调用、模块之间怎么依赖、改一行代码会波及哪里。结果就是：回答不准、建议片面、重构时漏改。

## Manon 怎么解决

Manon 为你的代码库构建**知识图谱** — 函数、类、调用关系、依赖链、模块结构，全部索引。然后把这些知识注入你的 AI 助手，让它在回答每个问题前，先查图谱、再开口。

```
你：这个项目的认证流程是怎样的？

AI（没有 Manon）：我看到当前文件有一个 login 函数...（只能猜）

AI（有 Manon）：认证流程涉及 3 个模块：
  → auth/middleware.py 的 JWTMiddleware 拦截请求
  → auth/service.py 的 authenticate() 验证 token
  → auth/provider.py 的 OAuthProvider 处理第三方登录
  共 12 个函数参与，调用链深度 4 层。
```

## 它能做什么

### 🔍 语义搜索
用自然语言找代码。不是关键词匹配，是理解你的意图。
> "处理支付失败的重试逻辑在哪？"

### 🕸️ 依赖分析
查任意符号的调用者、被调用者、继承关系、导入链。
> "谁在调用 DatabasePool.get_connection？"

### 💥 影响评估
改代码前，先知道会波及什么。
> "如果我重构 UserService，哪些模块会受影响？"

### 🧠 深度问答
多轮推理，自动拆解复杂问题，迭代查询直到答案完整。
> "这个项目的数据流是怎样的？从 API 请求到数据库写入。"

### 🚀 任务规划与执行（Pipeline）
提出需求，Manon 自动走完整个开发流程：需求澄清 → 规格生成 → 技术设计 → 任务拆解 → 逐步执行。
> "帮我给这个项目加上 WebSocket 实时通知功能"

## 谁适合用

- **使用 AI 编程助手的开发者** — 让 Claude Code / Cursor / Windsurf 从"看一个文件"升级到"懂整个项目"
- **接手陌生项目的人** — 快速理解架构，不用一个文件一个文件翻
- **重构前想评估风险的人** — 改之前就知道会影响什么
- **Tech Lead / 架构师** — 审查代码变更的影响范围

## 快速安装

```bash
git clone https://github.com/brandonzyy/manon-mcp.git
cd manon-mcp
bash install.sh
```

安装脚本会自动完成所有配置：检测你的编辑器 → 安装依赖 → 注册 MCP 服务 → 获取 API Key。

重启编辑器即可使用。

## 支持的编辑器

| 编辑器 | 激活方式 | 说明 |
|--------|---------|------|
| **Claude Code** | 输入 `/manon` | 自动进入 Manon 模式，所有代码问题走知识图谱 |
| **Cursor** | Composer 中直接可用 | 工具自动出现在 MCP 工具列表 |
| **Windsurf** | Cascade 中直接可用 | 工具自动出现在 MCP 工具列表 |

### Claude Code 示例

```
> /manon
✓ 项目已索引：142 个代码实体，89 组调用关系，256 个代码片段

> 这个项目怎么处理错误的？
（Manon 自动查询知识图谱，返回完整的错误处理链路）

> 如果我把 config.py 的 Settings 类拆成两个，会影响什么？
（Manon 分析调用图，列出所有受影响的模块和函数）
```

### Pipeline 示例

```
> 帮我加一个用户注册功能

Manon: 我先了解一下现有的用户模块...
       [查询知识图谱：用户相关代码]

       现有 auth/service.py 有登录逻辑，但没有注册。
       几个问题：
       1. 注册需要哪些字段？（邮箱/手机/用户名）
       2. 需要邮箱验证吗？

你: 邮箱+密码就行，不用验证

Manon: 好的，生成规格：
       ── 任务规格 ──
       [MUST] 邮箱+密码注册接口
       [MUST] 密码哈希存储
       [SHOULD] 邮箱唯一性校验
       确认这个方案吗？

你: 确认

Manon: [查询依赖图 → 设计方案 → 拆解任务 → 逐步执行]
       ✓ 任务 1/3: 添加 POST /register 端点
       ✓ 任务 2/3: 添加密码哈希工具函数
       ✓ 任务 3/3: 添加邮箱唯一性校验

       全部完成。改了 3 个文件，新增 1 个文件。
```

## 工作原理

```
你的电脑                              云端
──────────                           ──────
① 扫描项目文件
② 本地解析 AST
   （函数、类、调用、导入）
③ 计算文件哈希，只传变更的 ──────────→ ④ 构建知识图谱
                                      ⑤ 生成向量索引
                                      ⑥ 存储实体和关系
                                         ↓
⑧ AI 助手拿到完整上下文 ←──────────── ⑦ 响应查询请求
```

**关键设计：**

- **本地解析，云端存储** — 代码不需要 push 到 GitHub，本地改了就能查
- **增量同步** — 只上传变更的文件，不是每次全量扫描
- **知识图谱 + 向量搜索** — 既能精确查调用关系，也能模糊语义搜索

## 手动配置

如果不想用 `install.sh`，可以手动配置。

<details>
<summary>Claude Code</summary>

编辑 `~/.claude/settings.json`：

```json
{
  "mcpServers": {
    "manon": {
      "command": "/path/to/manon-mcp/.venv/bin/python",
"args": ["/path/to/manon/run_mcp.py"],
      "env": {
        "MANON_API_KEY": "msk_your_key"
      }
    }
  }
}
```
</details>

<details>
<summary>Cursor</summary>

编辑 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "manon": {
      "command": "/path/to/manon-mcp/.venv/bin/python",
"args": ["/path/to/manon/run_mcp.py"],
      "env": {
        "MANON_API_KEY": "msk_your_key"
      }
    }
  }
}
```
</details>

<details>
<summary>Windsurf</summary>

编辑 `~/.codeium/windsurf/mcp_config.json`：

```json
{
  "mcpServers": {
    "manon": {
      "command": "/path/to/manon-mcp/.venv/bin/python",
"args": ["/path/to/manon/run_mcp.py"],
      "env": {
        "MANON_API_KEY": "msk_your_key"
      }
    }
  }
}
```
</details>

## 环境要求

- Python 3.10+
- Claude Code、Cursor 或 Windsurf 任一
- 网络连接（连接 Manon 云端服务）

## License

MIT
