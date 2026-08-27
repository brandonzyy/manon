# Changelog

## [1.6.3] - 2026-08-27

### Added
- **manon 给自己装上了保障栈——体检从 1/14 到 13/14（唯一缺格是变异测试，
  P6 按序列刻意留位）。** 此前本仓零 CI、零 lint、零类型、零死代码、零依赖审计，
  发明 /assurance 的仓自己是裸的。
  - `scripts/check_l1.py` —— L1 五条棘轮（lint / 类型 / 死代码 / 契约死面 / 依赖
    漏洞）+ 清单闭合不变量，判据只此一份，CI 与本机同款。基线冻结于
    `scripts/l1-baselines/`：lint 116、typing 38（mypy `follow_imports=skip` 钉死
    跨机可比——本机生成与 CI 装依赖的两套解析深度会读出两套错误）、死代码 8、
    契约死面 13（诱饵旋钮与死端点入账，处置是退役或接线，不是豁免）、依赖漏洞 0
  - `.github/workflows/ci.yml` —— 机外执行器：干净克隆跑 check_skills + check_l1 +
    937 条测试带覆盖率测量（P5 第一步：只测量不设门槛）；gitleaks 独立 job 守密钥
  - `gates.txt` 门禁清单（2 登记 + 1 豁免闭合）、`ruff.toml` / `mypy.ini` /
    `scripts/requirements-l1.txt`（工具钉版本）
  - `docs/incidents/` 事故账开账：README 停更五个月、install.bat 烂三个版本
    两页，防复发各自落位（CONTRIBUTING 发版清单 + check_skills 双安装脚本校验）
  - `/assurance` 行为层审计收尾加一条：**审完把受影响主链路实际操作一遍**——
    静态读码判不掉运行时断点，这一步把「审计过了」和「产品好的」分开

### Changed
- `tests/e2e_mcp*.py|sh` 归位进 `tests/e2e/` 目录（零外部引用，体检的验收用例
  格此前因「是文件不是目录」看不到它们）

## [1.6.2] - 2026-08-27

### Changed
- **安装器收拢为四个平台：Claude Code / Codex / ZCode / Kimi Code。** Cursor、
  Windsurf、Zed、Continue、CodeBuddy、OpenCode 六个平台的检测与装块整体移除
  ——装块越多，每加一个 skill 要同步的面就越大（check_skills.py 守的就是这个），
  不再使用的平台留着只会分摊注意力。已装机器上这些平台的 MCP 配置不受影响，
  只是升级后不再自动配置。已有 key 探测清单与摘要输出同步收窄。

### Added
- **ZCode 与 Kimi Code 支持。** 两个平台的装法刻意不同——MCP 各写各的（ZCode 是
  `~/.zcode/cli/config.json` 的嵌套 `mcp.servers`，严格 schema 只写规范字段，且
  必须与已有的 plugin 开关等状态合并；Kimi Code 是 `~/.kimi-code/mcp.json` 的
  Claude 兼容 `mcpServers`），而 skill 装一份共享：两家的用户级目录都读
  `~/.agents/skills/`，再各留一份只会制造两处事实源。`/manon` 与 `/assurance`
  连同 references/、scripts/ 整树装入，已退役 skill 的壳同样从共享位摘掉；
  install.sh 与 install.bat 同步，check_skills.py 门禁过。

### Fixed
- codex 在 PATH 上而 `~/.codex` 不存在时，`cat >> config.toml` 直接被 `set -e`
  中断，安装走到一半死掉——补 `mkdir -p`（install.bat 的 codex 分支同款）。

## [1.6.1] - 2026-08-27

### Added
- **六件套的第六件：依赖与密钥。** 前五件审「你写的代码」，这件审「你带进来的」：
  依赖里的已知漏洞、误提交的凭据。vibe coding 里模型自己挑包（投毒包正是冲这个
  来的）、自己造长得像密钥的字符串——这条向量没有人的先验可依赖，只能机器守。
  - 判据.md 的五件套表加第六行（Python: pip-audit + detect-secrets/gitleaks；
    TS: npm audit / osv-scanner + gitleaks；Go: govulncheck + gitleaks），
    SKILL.md 的 Day-0 与读数纪律同步，「17/17」改「满格」——格子数会随体系长，
    硬编码的数字只会变成第二个事实源
  - `assurance_check.py` 加三格：依赖审计 (Python/TS)、密钥扫描。前两格配置面
    刻意宽松（执行器点名即算配置——pip-audit 这类工具没有独立配置文件）；密钥格
    保留 CONFIGURED_NOT_RUN 形态（gitleaks.toml / .secrets.baseline 在、没人跑）

### Fixed
- 回流今晨只改了本机副本的两条修正：package.json 的依赖段不算执行器面
  （`@stryker/core` 躺在 devDependencies 会让「变异测试 (TS)」假绿，而写在
  pyproject `[dependency-groups]` 里的 mutmut 诚实报黄——同一事实两个相反判定，
  假绿那一半更该修）；一次性登记兜底放宽的配套。补齐要点.md 同步补「判据实现有
  两份时 Step 1 自己会假红」一节：两副本不能靠 diff 全等守（判例匿名化），读数与
  记忆不符时先比两份检查器的代码差异。

## [1.6.0] - 2026-08-27

### Changed
- **skill 体系收拢成两个：/manon（激活）+ /assurance（工程保障唯一入口）。**
  1.5.x 结束时是 7 个 skill，拆成多个入口的代价和当年 /tc 一样：没人记得在什么时候
  进哪个门。收拢后 SKILL.md 的 Step 2 分诊表是唯一路由，旧入口的触发语全部收进
  assurance 的触发段。
  - **/dao、/audit、/retire-checks 并入 /assurance**，各自变成一条带 references 的循环：
    结构简化、行为层审计、检查退役。判据与流程原文迁移；scripts 原样搬进
    `assurance/scripts/`（dao 系列五个 + `retire_checks.py`），git mv 保留历史。
    dao 流程同步译成中文，与体系其余部分一种语言。
  - **/experience、/idea 退役。** 体验驱动验证与需求精化不再需要独立 skill 承载——
    今天的模型在普通对话里已经能做好这两件事，做成 skill 反而多一层要维护的壳
    （README 的「技能自增强循环」叙述同步改写）。
  - `install.sh` 装块合并为一个 assurance 块；退役 skill 的壳（tc / dao / audit /
    retire-checks / experience / idea）安装时主动摘除，理由同 1.5.0 摘 tc。
  - `判据.md` 的 L3 行、`dao-report.py` 的报告落款同步改指新去处；
    CLAUDE.md 的 /experience 章节删除。

### Fixed
- CHANGELOG 1.4.1 / 1.4.0 两条的版本标题此前丢失（只剩「 - 2026-08-24」），补回。
- README / README_CN 自 1.2.4 起未随 skill 演进更新：/tc、/exp 等已不存在的命令仍在被
  宣传，版本历史缺 1.4/1.5 整段。本次随整合重写 skill 章节、补齐版本表；
  docs/PRODUCT.md 的技能章节同步重写为两技能结构。

## [1.5.1] - 2026-08-26

### Added
- **`/retire-checks` 进仓。** 1.5.0 漏了它，而 `/assurance` 的分诊表里有三处指向它
  （description、触发段、Step 2）——**assurance 的卖点就是「按读数分诊」，三个去处
  之一在仓里不存在**，装了的人点过去是空的。当时我把同一个问题按两种方式处理了：
  `/audit` 里那行改成不许诺，assurance 里三处却留着。该补的是 skill，不是措辞。
  - 按「决策棘轮 / 行为回归 / 元账」三分类，证明后再删，并装棘轮防复发
  - `scripts/retire_checks.py`（纯 stdlib）：`inventory` / `blast-radius` / `vacuous`
    / `snapshot` / `diff`，清点与算数全走脚本，LLM 只做判断
  - 删之前必须过的四道证明：覆盖不丢、引用不悬空、**不制造空断言**
    （被删对象若是另一条门禁的扫描目标，删了它那条门禁会静默变 PASS）、不是唯一执行者

- **`scripts/check_skills.py` —— skills/ 的两条不变量，两条都注入验红过。**
  上面那个洞是手工发现的，而它属于「不会报错、只会静默不成立」的一类，该有执行器：
  1. **install.sh 的装块必须覆盖 skill 的每一个文件。** 少 cp 一类文件，下次安装就把
     SKILL.md 覆盖回仓里的版本，而没被覆盖的那些原样留着，变成**没有任何 SKILL.md
     指向的孤儿**——看着还在，实际没人读，全程零报错。1.5.0 修的正是这个（dao/audit）。
  2. **skill 之间的交叉引用必须指向本仓真有的 skill。** 就是这次的洞。

### Fixed
- `/audit` 分工表里 `/retire-checks` 那行恢复（1.5.0 里因为它不在仓中被改成了不许诺）。
- `assurance_check.py` 的判例注释与 `覆盖循环.md` 里还指着 `skills/tc/` 的说法改掉——
  该目录已于 1.5.0 删除，留着会让人去找一个不存在的路径。

## [1.5.0] - 2026-08-26

### Added
- **新 skill `/assurance` —— 工程保障体系的入口。** 先给项目打三态分
  （OK / 配了没跑 / 缺），再按读数分诊：缺层就走「白捡→死面→CI→类型→覆盖→变异」
  一阶段一个终态地补，全绿则转 `/retire-checks` 减死重、走覆盖循环加覆盖、
  `/audit` 找行为层缺陷。它要防的头号失效是 **`CONFIGURED_NOT_RUN`（配了没跑）**：
  配置文件里躺着完整工具配置、全仓零执行器、二进制甚至没装——**它看起来像装了**，
  而反复审计多轮都抓不到，因为审计的眼睛不往配置文件里看。
  - `scripts/assurance_check.py` —— 三态体检，按四层保证栈 + 两条元规则出表
  - `scripts/coverage_targets.py` —— 覆盖率读数与补测试目标排序
  - `references/判据.md` —— 四层保证栈、两条元规则、缺陷沉降（skill 自带判据，可独立运转）

### Changed
- **`/tc` 的覆盖循环并入 `/assurance` 的 P5，`/tc` 退役。** 覆盖率循环本来就是补齐序列
  的内部动作；拆成独立入口的代价是没人在读数之后被指向它——实测 200+ 个会话里 `/tc`
  零调用，而它被三个在用的 skill 引为配对。重写的 `coverage_targets.py` 修掉了原
  `tc-scan.py` / `tc-commit.py` 的七处问题，每一处的失败形态都是**静默的**：

  1. `tc-commit.py` 裸 `git commit -m`（无 pathspec）提交整个索引——共用工作树上会把
     别人暂存的东西一起带走。新工具**完全不碰 git**。
  2. 覆盖率重跑失败被 `except Exception: pass` 吞掉，之后照样打印 `coverage_after`
     ——报的是**陈旧读数**，方向偏高。
  3. 图谱 API 失败静默返回 `{}` → `fan_in` 记 0 → **重要目标被降权到最后**，
     而输出看起来完全正常。新工具拿不到就是 `null`，并在 `warnings` 里说明。
  4. 找不到 lcov 时返回 `{"targets": []}` + 一句 hint——**分析失败被呈现成
     「已经覆盖完了」**。新工具退出码 2。
  5. 优先级公式给 fan-in 0–1 一律 1x 权重，即**给零调用方的代码排目标**。
     给零消费者的代码补测试是给它背书，还会让它从死面棘轮里消失。
     新工具默认把 `fan_in == 0` 移出目标、单列 `dead_candidates`。
  6. `targets[:50]` / `[:20]` 静默截断，读起来像「全看过了」。新工具在 `dropped` 里报。
  7. 硬编码某个具体仓的 lcov 路径，整条链钉死 bun/TypeScript。新工具同时读 lcov 与
     Go coverprofile。

- **新增判据「分母自证」**：覆盖工具通常只报**被测试 import 过**的文件，没被 import 的
  连 0% 都不出现，于是百分比是在一个子集上算的、显著偏高。外部参照取 `os.walk` 数磁盘
  源文件数——**在被检查的机制之外**，覆盖工具自己报的文件数不能给自己作证。比值 < 0.9 退 2。

- `install.sh` 装 `/assurance`。**注意它有 `references/` 与 `scripts/`，两者都必须装**：
  只装 SKILL.md 会留下一个链向不存在文件的入口，而且没有任何报错。

- **`/dao` 与 `/audit` 的 SKILL.md 压到 100 行以内，超出的部分抽进 `references/`。**
  SKILL.md 命中即整份进上下文，而 `/audit` 原来是 172 行、`/dao` 134 行，其中大半是
  只在执行到某一步才需要的判据（五类缺陷谱系的逐条审计手法、简化分类学）。
  抽出去之后按需读，入口本身变薄。**逐行比对确认没有内容丢失**（dao 未命中 0 行，
  audit 3 行是 `/tc` 退役后的指向改写）。

- **`install.sh` 补上 `/dao` 与 `/audit` 的 `references/` 拷贝。** 这两个装块原先只
  `cp SKILL.md`——**这正是上一条会静默失效的原因**：SKILL.md 退回上游版本、本地抽出的
  `references/` 变成没人引用的孤儿文件，看着还在，实际已不被任何 SKILL.md 指向，
  而整个过程不报一个错。

### Removed
- **`skills/tc/` 与它的安装块删除。** 覆盖循环已并进 `/assurance` 的 P5（见上）。
  安装脚本改为主动 `rm -rf ~/.claude/skills/tc`——装过老版本的机器上那个壳还留着，
  而留一个不再被任何文档指向的壳，下一个人会以为它还在维护。

## [1.4.3] - 2026-08-24

### Fixed
继续在 CaseOS 上逐条人工核对 1.4.2 的输出。21 条死面里有 12 条是假的，分三类，
每一类都会诱导人去删活着的东西——这正是死面表最危险的失败模式。

- **schema 被当成快照读，而它是一串迁移**：`003` 建表、`058` 删表，CHECK 字面量永远留在
  `003` 里，于是**已经退役**的表，它的每个状态值都被永久报成死值。CaseOS 上 13 条死状态里
  有 4 条属于这类（`tool_action_requests` / `assistant_proposals` / `assistant_weekly_reports`
  已被 058 删除，`finance_journal_entries` 被 047 删除）。现在按迁移顺序对账
  `DROP TABLE` / `DROP COLUMN`，被删之后没有重建的表列不再进表
- **SQL 文件整体不算消费者**：为了不让 CHECK 声明算自己的用例，之前跳过全部 `.sql`。
  但一个 `.sql` 文件里只有声明那几行是声明，其余的 seed 行、回填、
  `DELETE ... WHERE status='x'` 都是真实的写入方和读取方。改为**按行**排除声明跨越的行
- **`env_prefix` 绑定的 env 名在源码里根本不出现**：pydantic 的
  `SettingsConfigDict(env_prefix="CASEOS_")` 把 `CASEOS_OUTBOX_WORKER_ENABLED` 绑到字段
  `outbox_worker_enabled`，全仓搜不到那个 env 名。之前判它是「诱饵旋钮」——而它控制着
  一个后台 worker，照着删就把开关删了
- **`ADD COLUMN x ... DEFAULT 'v'` 的默认值绑错了列**：默认值的正则没有锚在列定义开头，
  于是行首第一个词赢——默认值被绑到一个叫 "ADD" 的列上，真正的列丢了默认值，
  一个由数据库自己回填的值被报成「代码零引用，可以删」
- **`DROP CONSTRAINT, ADD CONSTRAINT` 是重定义，不是追加**：之前把历次声明取并集。
  并集对「放宽」是对的，对「收窄」正好相反——刚被迁移移除的值永远留在允许集里，
  于是它会在有人已经退役它之后继续被报死。同时，一条 ALTER 里往往有两个 CHECK：
  真正的词表，和一条 scope 规则（分支里写 `kind IN ('main','chat')`）。后者是**谓词**
  不是声明，按列合并后再整体替换，否则重定义会把词表缩成规则的一个分支

### Added
- **状态值表新增反向判据：代码写入的值不在 schema 允许集**。这是这张表里唯一一条
  「确定」而非「疑似」的结论——那条语句必然被 CHECK 拒绝。它通常被 `try/except` 包着记一行
  warning，于是恰恰在最需要它的时候静默失败。CaseOS 首跑就抓到一条真的：
  `worker_supervision.py` 写 `service_heartbeats.status='error'`，而 CHECK 只收
  `ready/degraded/stopped`——worker 崩溃循环时的错误心跳从来没有落库过
- 语句边界按子句收敛（`WHERE` / `RETURNING` / `;` / 三引号 / 硬上限）。宿主字符串里的 SQL
  没有结尾分号，不收敛的话一条 `UPDATE` 会吞掉整个文件——这条判据的第一版就是这样
  从 1 条真结论变成 333 条噪音的

### Changed
- contract audit 测试 38 → 55（新增 17 条：迁移生命周期 4、seed 即写入 2、
  写入越界 4、env_prefix 3、ADD COLUMN 默认值 1、约束重定义 2、scope 谓词 1）
- CaseOS 实测：死面 21 → 15（12 假阳性消除、1 真缺陷新增），耗时不变（~3.4s / 1061 文件）。
  按这 15 条清理完并登记 2 条豁免后归零

## [1.4.2] - 2026-08-24

### Fixed
- **策略文件把自己算成了证据**：`.manon-contract.yaml` 会逐条列出它豁免的 id，
  留在语料里就成了「有人引用」——第一个认真写豁免清单的人，会看着整张表静默归零。
  策略文件现在整体不进语料。CaseOS 上实测：写完清单后 21 死面被误报成 0，修复后回到 21。

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

## [1.4.0] - 2026-08-24

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
