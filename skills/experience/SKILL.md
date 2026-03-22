---
name: experience
description: /experience -- 体验驱动开发循环，AI 像真实用户一样操作产品验证功能
user_invocable: true
---

# Experience Loop — 体验驱动开发循环

**核心理念**：开发完成后，AI 像真实用户一样操作产品，发现问题 → 修复 → 重测，循环迭代直到功能完善。

---

## Phase 1: DEFINE（生成 Experience Spec）

从用户描述生成结构化测试规格。

**如果用户直接给了具体场景描述**，跳过追问直接生成 spec。
**如果用户只说了模糊需求**（如"测试聊天功能"），追问 1-2 个关键问题。

### 判断产品类型

根据上下文自动判断，也可由用户指定：

| type | 判断依据 | 主要工具 |
|------|---------|---------|
| `web` | 有 URL、前端页面、HTML | Playwright MCP（browser_*） |
| `cli` | 命令行工具、脚本、终端交互 | Bash（运行命令、观察 stdout/stderr） |
| `service` | 后端服务、API、WebSocket | Bash（curl/httpx + 日志 tail） |
| `hybrid` | Web 前端 + 后端联动 | Playwright MCP + Bash |

### Spec 格式

```yaml
feature: <功能名称>
type: web | cli | service | hybrid
entry:
  url: http://localhost:3600        # web/hybrid
  command: "python cli.py"          # cli
  service_url: http://localhost:3700 # service/hybrid
  log_file: /path/to/app.log       # 可选，指定日志文件
  log_command: "docker logs -f app" # 可选，日志获取命令
preconditions:
  - <前置条件列表>
scenarios:
  - id: S1
    name: <场景名>
    priority: MUST | SHOULD | MAY
    description: |
      <自然语言描述操作步骤和预期结果>
      （不是脚本，LLM 自己决定怎么操作、验证什么）
```

**向用户展示 spec，等待确认或修改后继续。**

---

## Phase 1.5: AUDIT（日志可观测性审计）

**目的**：在启动体验测试之前，确认代码在场景涉及的关键路径上有足够的日志。没日志的代码，agent 操作了也无法判断后端到底发生了什么。

**仅 `cli` / `service` / `hybrid` 类型需要此步骤。`web` 类型跳过（Playwright snapshot 已提供充分的前端可观测性）。**

### 执行步骤

1. **从 spec 提取关键路径**：根据 scenarios 的 description，识别会触发的核心函数/模块/API 端点。

2. **用 Manon 搜索日志覆盖**：
   ```
   manon_search(repo_id, "logging in <关键模块>")
   manon_graph(repo_id, "<入口函数>", "callees")
   ```
   沿调用链检查：入口 → 核心逻辑 → 外部调用（DB/API/文件），每个环节是否有日志。

3. **逐文件快速扫描**：对关键路径涉及的文件，检查是否存在 `log.`/`logger.`/`logging.`/`console.log`/`print` 等日志调用。

4. **生成审计报告**，展示给用户：

```
## 日志审计 — {feature}

| 关键路径 | 文件 | 日志覆盖 | 缺失点 |
|---------|------|---------|-------|
| 请求入口 | api/router.py:handle_search | OK | — |
| 核心逻辑 | core/search.py:execute | MISSING | 无搜索参数/结果日志 |
| DB 查询 | core/db.py:query | PARTIAL | 有错误日志，缺成功路径 |
| 响应返回 | api/router.py:handle_search | OK | — |
```

5. **决策**：
   - **全部 OK** → 直接进入 Phase 2
   - **有 MISSING/PARTIAL** → 列出需要补充的日志点，询问用户：
     > "以下关键路径缺少日志，Experience Agent 将无法验证这些环节的行为。是否先补充日志再测试？"
   - 用户同意 → 补充日志（遵循下面的日志规范），补完后进入 Phase 2
   - 用户拒绝 → 标注 spec 中哪些场景验证能力受限，继续 Phase 2

### 日志补充规范

补日志时遵循项目已有风格（`logging` / `print` / `console.log`），不引入新依赖。

**必须有日志的位置**：
- 函数入口：关键参数（脱敏）
- 分支决策：走了哪个 if/else，为什么
- 外部调用前后：请求参数 + 响应状态/耗时
- 异常捕获：完整 error + context
- 函数出口：关键返回值/状态

**日志格式**：
```python
# Python 示例
log.info("search: query=%s top_k=%d", query, top_k)
log.info("search: found %d results in %.2fs", len(results), elapsed)
log.error("search failed: %s | query=%s", exc, query)
```

**不做**：
- 不记录完整请求体/响应体（太大）
- 不记录密钥/token/密码
- 不在热循环内逐条记录（性能）

---

## Phase 2: EXPERIENCE（启动 Experience Agent）

用户确认 spec 后，根据 `type` 构造 subagent prompt，调用 `Agent` tool。

### Agent Prompt 模板

按产品类型组装工具说明段，其余部分通用。

````
你是产品体验测试员。你的任务是像真实用户一样操作产品，验证功能是否正常工作。

## 可用工具

{tools_section}

### 代码定位（Manon MCP）
- `manon_search(repo_id, query)` — 语义搜索代码
- `manon_graph(repo_id, symbol, direction)` — 查调用关系

## 你要测试的功能

{experience_spec_yaml}

## 执行规则

{rules_section}

### 通用规则
1. 按每个 scenario 的 description 逐步操作
2. **每步操作后必须验证结果**（snapshot / 检查输出 / 读日志），不假设成功
3. 遇到问题立即记录，继续下一步
4. 如果某场景被前序失败阻塞，标记为 BLOCKED
5. 对失败场景，用 `manon_search` 定位可能的代码位置
6. **你不修改代码，只报告问题**

## 输出格式

完成所有场景后，输出 JSON 格式的 experience_report：

```json
{
  "feature": "<功能名>",
  "type": "<web|cli|service|hybrid>",
  "round": 1,
  "scenarios": [
    {
      "id": "S1",
      "name": "<场景名>",
      "status": "PASS | FAIL | BLOCKED | SKIPPED",
      "steps_taken": ["<步骤1>", "<步骤2>"],
      "observation": "<实际观察到的结果>",
      "expected": "<预期结果>",
      "log_evidence": "<相关日志片段（如有）>",
      "suspected_cause": "<可能的原因（仅 FAIL 时）>",
      "code_location": "<代码位置（仅 FAIL 时，通过 manon_search 定位）>"
    }
  ],
  "summary": {
    "total": 0,
    "passed": 0,
    "failed": 0,
    "blocked": 0,
    "skipped": 0
  }
}
```

## 重要提醒

- 结果必须真实，不要猜测"应该能工作"
- 每步都验证，不要假设操作成功
- MUST 优先级场景必须测试，MAY 场景可在时间紧张时跳过
- 日志是关键证据——看到异常日志即使表面"正常"也要记录
````

### 按 type 插入的 tools_section

**type: web**
```
### 浏览器操控（Playwright MCP）
- `browser_navigate(url)` — 打开页面
- `browser_snapshot()` — 获取页面无障碍树（理解当前页面结构）
- `browser_click(element, ref)` — 点击元素（ref 来自 snapshot）
- `browser_type(element, ref, text)` — 在元素中输入文本
- `browser_press_key(key)` — 按键（如 Enter）
- `browser_wait_for(time)` — 等待指定毫秒
```

**type: cli**
```
### 终端操控（Bash）
- `Bash(command)` — 运行命令，观察 stdout/stderr
- 可以运行交互式命令（通过 echo/printf 管道输入）
- 可以用 `timeout` 限制长时间运行的命令
- 可以用 `tail -f <log_file> &` 在后台监听日志
- 可以用 `cat`/`grep` 检查输出文件
```

**type: service**
```
### 终端操控（Bash）
- `Bash(command)` — 运行命令
- 发请求：`curl -s -X POST <url> -H 'Content-Type: application/json' -d '<body>'`
- 查日志：`tail -n 50 <log_file>` 或 `<log_command>`
- 查进程：`ps aux | grep <process>` / `ss -tlnp | grep <port>`
- WebSocket：`websocat ws://host:port/path` （如可用）
- 检查响应状态码、JSON 结构、错误信息
```

**type: hybrid**
```
### 浏览器操控（Playwright MCP）
- `browser_navigate(url)` — 打开页面
- `browser_snapshot()` — 获取页面无障碍树
- `browser_click(element, ref)` — 点击元素
- `browser_type(element, ref, text)` — 输入文本
- `browser_press_key(key)` — 按键
- `browser_wait_for(time)` — 等待

### 终端操控（Bash）— 后端验证
- `Bash(command)` — 运行命令
- 查后端日志：`tail -n 50 <log_file>` 或 `<log_command>`
- 查 API 响应：`curl -s <service_url>/endpoint`
- 查进程状态：`ps aux | grep <process>`

### 联动验证
- 前端操作后，立即检查后端日志确认请求是否到达
- API 返回异常时，同时检查前端是否有错误提示
- WebSocket 场景：前端发消息 → 检查后端日志 → 验证前端收到响应
```

### 按 type 插入的 rules_section

**type: web**
```
1. `browser_navigate` 打开 URL
2. `browser_snapshot` 了解页面结构
3. 每次操作后 `browser_snapshot` 确认结果
4. 页面加载失败 → 等待 5 秒重试一次
5. 意外弹窗/对话框 → 记录并尝试关闭后继续
```

**type: cli**
```
1. 先确认命令/工具存在（`which`/`--version`）
2. 运行命令，完整捕获 stdout 和 stderr
3. 检查退出码（$?）— 非零即异常
4. 对长时间运行的命令用 `timeout 30s <command>` 限制
5. 如有日志文件，操作前后对比日志内容
```

**type: service**
```
1. 先确认服务在运行（`curl <service_url>/health` 或 `ss -tlnp | grep <port>`）
2. 如果服务未启动，尝试启动并等待就绪
3. 发请求前先 `tail -n 0 -f <log_file> > /tmp/exp_log &` 开始捕获日志
4. 发送请求，检查 HTTP 状态码和响应体
5. 操作后读取捕获的日志，确认后端行为正确
6. 日志中出现 ERROR/WARN/Exception/Traceback → 记录为证据
```

**type: hybrid**
```
1. 先确认后端服务在运行
2. 开始捕获后端日志（后台 tail）
3. `browser_navigate` 打开前端
4. 每次前端操作后：
   a. `browser_snapshot` 确认前端状态
   b. 检查后端日志确认请求到达和处理结果
5. 前后端观察不一致时，两边证据都记录
```

**Agent 调用参数**：
- `subagent_type`: 不指定（使用 general-purpose）
- `description`: "Experience: {feature名称}"
- `prompt`: 按上述模板组装的完整 prompt

---

## Phase 3: FIX（解析报告 + 修复）

Agent 返回后：

1. **解析 experience_report JSON**（从 agent 返回文本中提取）
2. **展示报告给用户**，格式：

```
## Experience Report — Round {n} [{type}]

| 场景 | 状态 | 观察 |
|------|------|------|
| S1: xxx | FAIL | xxx |
| S2: xxx | PASS | — |

通过: X / 失败: Y / 阻塞: Z
```

3. **如果全部 PASS** → 体验验证通过！流程结束。
4. **如果有 FAIL**：
   - 根据报告中的 `code_location`、`suspected_cause`、`log_evidence` 定位代码
   - 修复问题（正常使用 Read/Edit 工具）
   - 修复完成后进入 Phase 4

---

## Phase 4: RE-EXPERIENCE（重测循环）

1. **轮次计数**：当前是第几轮（从 1 开始）
2. **最多 3 轮**：
   - 第 3 轮仍有 FAIL → 停止自动修复，向用户报告剩余问题，让用户决策
3. **只重测失败场景**：将上轮 FAIL 和 BLOCKED 的场景重新组成 spec，回到 Phase 2
4. **累积 PASS**：已通过的场景不再重测

---

## 约束

- **不做**：性能测试、跨浏览器兼容、安全测试
- **互补关系**：不替代单元测试/集成测试，聚焦端到端用户体验
- **日志依赖**：service/hybrid 类型要求代码有详细日志，无日志时 agent 只能通过输出判断
- **超时**：单个场景操作超过 60 秒无响应视为 FAIL
