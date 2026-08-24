# Changelog

## [1.4.1] - 2026-08-24

### Fixed
在 CaseOS 上逐条人工核对 1.4.0 的输出时找到的四类假阳性。四条都会把活着的东西报成死面，
而一张报错的死面表比没有表更糟——它会训练人忽略它。

- **同文件调用方被整体排除**：为了不让路由定义算自己的调用方，之前排除了整个定义文件。
  但发链接的那个 handler 常常就在隔壁——`return {"url": f"/api/v1/employee/artifact-links/{token}"}`
  与被调端点同文件。改为只排除定义**所覆盖的行**（含跨行装饰器的每一行）
- **常量的同模块使用没算**：`LOGGER` 在 config.py 内部用了 6 次、`DEFAULT_..._TIMEOUT_MS`
  在同文件被代入默认值，之前只看别的文件，全被判死
- **MIME 类型被当状态值**：`state_columns` 按片段匹配，`type` 会带进 `media_type` /
  `content_type`，于是 `application/octet-stream` 成了「死状态」。含 `/` 或空格的值不是状态
- **列 DEFAULT 值被标成「零引用死值」**：DB 自己会写它，零引用意味着**没人读**，
  与「没人写」是两种缺陷、两种修法。改判为「只写不读」

### Changed
- 新增 6 条回归测试（每类假阳性一条正例 + 一条反例），contract audit 测试 31 → 37

 - 2026-08-24

### Added
- **契约对账（contract audit）** — 四张确定性对账表，补上图谱看不见的那类事实。
  图谱答「谁调用谁」，答不了跨语言/跨进程/跨部署那些**靠字符串连起来的边**，
  而死面正是在那里积累的。
  - `endpoints` — 后端声明的路由 ↔ 任何人调用的 URL
  - `configs` — 声明的旋钮 ↔ 真正读它的代码（诱饵旋钮、只向下游传播的死变量）
  - `states` — schema 允许的状态值 ↔ 代码写的和读的（死状态、幻想状态）
  - `envelope` — 路由入口 → 敏感汇点，中间有没有经过门禁（用本地调用图做可达性）
- `manon_contract_audit` MCP 工具（纯本地，不走服务端）
- `scripts/manon-contract-audit.py` CLI —— **零模型零服务端**，供 CI 与 git hook 直接调用；
  `--fail-on new` 只在新增死面时失败，接入当天不会挡住所有人的 push
- `/audit` skill —— 先用对账表划范围，再按五类缺陷谱系（假成功 / 守卫失效 /
  闭环无证据 / 契约错位 / 死面）做语义审计；每条 finding 以其**负向用例**为完成判据
- push hook 增量播报：首轮静默建基线，之后只报**新增**死面
- `.manon-contract.yaml` 本地判据文件（见 `manon-contract.yaml.example`）——
  事实全局、判据本地。豁免必须带 reason；腐坏的豁免（今轮没匹配到任何东西）会被单独报出来

### Design notes
- 对账结果**不进健康评分**。`WEIGHTS` 是总和 100 的定额，加一维就要给现有八维重新分配，
  历史分数全部失效不可比。分数答「形状」，对账表答「面还在不在」，两件事分开。
- 前三张表不碰图谱 schema：它们是「定义集 ∖ 消费集」的集合运算，跑在文件列表上即可。
  只有 `envelope` 用到调用图，而它用的是本地 `codeindex.parser`，不需要服务端。
- 审计的文件口径**宽于**索引口径：`scripts/` 被 `_TOOL_DIRS` 当 tool_script 丢弃后
  不进图谱，但门禁逻辑就住在那里 —— 对账必须看得见它。

### Fixed
- 契约对账复用 `core/ast/config._should_auto_exclude_dir`，正确跳过 `.venv-p0`
  这类带后缀的虚拟环境目录（某仓因此少扫 13199 个文件，18.5s → 1.2s，
  并消除了 4 个来自环境内旧版包的假死端点）

## [1.2.2] - 2026-03-21

### Fixed
- **Critical**: Fixed `install.sh` crash (`DEFAULT_API_URL: unbound variable`) — API_URL assignment moved after region detection (`8d6920c`)
- Fixed broken Windows `set` syntax for `MANON_DIR` in skill scripts (`6694a28`)
- Eliminated phantom nodes and empty-caller edges in knowledge graph (`adf882a`)
- Scoped dao stop hook to current session via CWD match + 6h TTL (`4048625`)

### Added
- TypeScript/JS coverage support in `manon-scan-tests.py` (`fbfede0`)
- `dao-analyze.py` synced to global skill install (`254a850`)

### Improved
- Scan performance: mtime+size fast path skips unchanged files; partial parse on syntax errors (`96b58f0`)

### Docs
- Updated SKILL.md with ANALYZER/COMMITTER scripts and execution flow (`e147f97`)
- Added comment for custom tree-sitter-typescript fork (`beafd15`)

## [1.0.0] - 2026-03-16

### Changed
- **BREAKING**: Removed the legacy `shared/` package; server/client runtime code now lives under `core/`
- **BREAKING**: Renamed the local MCP package from `mcp/` to `manon_mcp/` to eliminate package-name conflicts
- Split query orchestration into `application/` services and reduced router/tool-layer business logic

### Improved
- Simplified MCP startup and registration by removing dynamic sibling/tool loading
- Added local runtime path management for SaaS state under `.manon_runtime/saas`
- Unified release version to `1.0.0` across MCP, SaaS, installers, and deployment scripts
- Updated `r760` deployment packaging to include `application/`, `core/`, and embedded `codeindex/`

### Fixed
- Fixed local impact analysis compatibility with `line_start` / `line_end` symbol fields
- Restored compatibility progress helpers in MCP sync workflows
- Kept end-to-end MCP init/scan/upload/query flow working after the architecture refactor

## [0.2.2] - 2026-03-07

### Changed
- **BREAKING**: Embedded codeindex into the repository `codeindex/` package
- Removed external codeindex dependency from requirements.txt
- All imports should now use `codeindex.*`

### Improved
- Fast language detection with `max_files=500` limit (0.01s vs 30s+)
- Parser installation timeout reduced to 30s with PyPI-first strategy
- Memory caching for language detection to avoid repeated scans
- Direct control over codeindex optimizations

### Fixed
- **Critical**: Fixed manon_init hanging caused by parameter mismatch with external codeindex
- No more version conflicts between Manon and external codeindex package

## [0.2.1] - 2026-03-07

### Changed
- Migrated to brandonzyy/codeindex fork with enhanced language detection
- Automatic language detection now supports `.mjs` files
- Automatic tree-sitter parser installation

### Improved
- Simplified codebase by removing 70+ lines of duplicate code
- `_load_scan_config` now uses `Config.load_with_auto_setup()`
- `ensure_parsers` delegates to codeindex built-in functions

### Fixed
- Language detection now correctly identifies JavaScript/TypeScript projects

## [0.2.0] - 2026-02-23

Initial release with MCP integration and knowledge graph support.
