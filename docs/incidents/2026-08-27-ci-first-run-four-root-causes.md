# CI 首跑即红：一处故障，四个独立根因

1. **发生了什么**：1.6.3（2026-08-27）把 gates 工作流推上 GitHub，`l1-and-tests`
   首跑即红。本机同款工具链五条棘轮全绿，红只在 CI 的干净环境出现。排查发现
   红色背后叠着四个互相独立的缺陷，任何单独修掉都会在下一个那里继续红：
   - **mypy fatal（exit 2）**：CI 装了产品依赖，mypy 在 `python_version=3.10`
     语义下解析 numpy 自带的 `__init__.pyi`，其中的 PEP 695 `type` 语句是语法
     错误。本机 `~/.claude/.venv-l1` 没有产品依赖，numpy 根本不进解析路径，
     所以本机永远绿——正是 mypy.ini 注释里自己预言过的「两边解析深度不同，
     同一份代码读出两套错误」。
   - **mypy 的 fatal 打在 stdout**：check_l1.py 崩溃时只回显 stderr，CI 上的
     死因显示为空，排障信息被吞。
   - **pytest 从没在 CI 形态下跑过**：`mcp` 没钉上界，新解析装到 2.x（FastMCP
     改名）import 全断；`pytest-asyncio` 不在任何 requirements 里（pytest 本身
     都是 pytest-cov 传递带进来的），48 个异步测试全挂；`tree-sitter-go` 本机
     venv 有、清单没有，干净环境必挂一个。
   - **contract 棘轮的审计面机器相关且自指**（1.6.4 修完前三个后 CI 复红，
     同一判据读数在 8/11/13 之间翻转）。三层同病：① `scripts/l1-baselines/
     contract.txt` 本身在扫描面里——基线写着 `endpoints:GET /tunnel-url`，
     下一轮审计把这行字当弱引用，dead 升 suspect，判定随基线内容自我翻转；
     ② 审计继承 `.manon_runtime` 里**用户为本机索引**配的 custom_excludes
     （本机配了 `**/scripts/**`），`launch_mcp.sh` 对 /tunnel-url 的引用被整片
     蒸发，两条 dead 凭空出现——CI 没有这份运行时配置，读的是另一个世界；
     ③ gitignore 的私有前端（`web/`）只在 本机进扫描面，改变 doc 档证据的
     权重，同一份代码两台机器读出两套 verdict。
2. **为什么没被拦住**：CI 是第一次真实运行——此前所有绿都出自本机环境，而
   本机环境恰好把四个坑全部遮住（无产品依赖、mcp 1.27、手动装过的
   tree-sitter-go、带着私有 web/ 与 runtime 配置生成基线）。元规则二说的
   「从干净克隆、在别人机器上跑」，一跑抓出四个，这正是它存在的理由。
   另有一层方法论教训：**验证「跨机可比」时必须连解释器一起换**——只把
   工具链 venv 挂上 PATH、仍用本机 python3 跑 check_l1，得出来的是假绿
   （本次排障中实测踩到）。
3. **防复发**：
   - CI 装依赖顺序改为「L1 工具链 → L1 检查 → 产品依赖 → pytest」，顺序本身
     是判据：L1 检查跑在产品依赖进环境之前，与 baseline 生成环境同构；
   - check_l1.py 三处 fatal（ruff/mypy/vulture）改为 `stderr or stdout` 回显；
   - `mcp>=1.0.0,<2`、`pytest-asyncio==1.4.0`、`tree-sitter-go` 进 requirements；
   - contract 审计面三条边界：`--exclude` 把棘轮自己的基线产物排除出证据
     （判据的输出不是证据）；`--no-project-excludes` 让棘轮审计**版本化的
     仓库**而非本机运行时配置（check_l1 已传）；`enumerate_files` 在 git 工作
     树内钉在「跟踪文件 + 未跟踪未忽略」面上（机器私有的 gitignore 树不再
     改写 verdict），非 git 树回退全量走查。基线在 CI 同构树上重造：13 → 11
     （tunnel-url 两条本就被 launch_mcp.sh 引用，是 runtime 配置制造的假 dead）。
   - 验证：Python 3.12 干净树与 3.14 本机树各三连跑全绿（11==11），pytest
     937 过（3.14）/ 929 过（3.12），五条棘轮与 baseline 逐条一致。

**补记（2026-08-28）**：上面第 2 条那句方法论教训当时只落在纸上——「必须连解释器
一起换」没有执行器，于是它照常复发：CLAUDE.md 与 CONTRIBUTING.md 写的都是裸
`python3 scripts/check_l1.py`，照着跑多出 6 条 `import-untyped`。真正的代价不是多
一条红，是下一步顺手 `--regenerate` 把幻影写进 baseline，CI 随后以「变少了」再红
一次，而两次红看起来都像真的。现补执行器：`check_l1.py` 起手查产品依赖在不在场
（哨兵 `PRODUCT_ONLY`），在场即拒；逃生口 `MANON_L1_ALLOW_DIRTY=1` 只放行读数、
对 `--regenerate` 一律无效。判据在 `tests/test_check_l1_env.py`（15 格），含一格
反向不变量：哨兵在真 L1 venv 里必须读作干净，否则红的是用例而不是那道门禁。
