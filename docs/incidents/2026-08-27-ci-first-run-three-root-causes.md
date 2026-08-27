# CI 首跑即红：一处故障，三个独立根因

1. **发生了什么**：1.6.3（2026-08-27）把 gates 工作流推上 GitHub，`l1-and-tests`
   首跑即红。本机同款工具链五条棘轮全绿，红只在 CI 的干净环境出现。排查发现
   红色背后叠着三个互相独立的缺陷，任何一个单独修掉都会在下一个那里继续红：
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
2. **为什么没被拦住**：CI 是第一次真实运行——此前所有绿都出自本机环境，而
   本机环境恰好把三个坑全部遮住（无产品依赖、mcp 1.27、手动装过的
   tree-sitter-go）。元规则二说的「从干净克隆、在别人机器上跑」，一跑就
   抓出三个，这正是它存在的理由。
3. **防复发**：
   - CI 装依赖顺序改为「L1 工具链 → L1 检查 → 产品依赖 → pytest」，顺序本身
     是判据：L1 检查跑在产品依赖进环境之前，与 baseline 生成环境同构；
   - check_l1.py 三处 fatal（ruff/mypy/vulture）改为 `stderr or stdout` 回显；
   - `mcp>=1.0.0,<2`、`pytest-asyncio==1.4.0`、`tree-sitter-go` 进 requirements。
   验证：Python 3.12 干净环境按新顺序走全流程，929 过 / 0 挂，五条棘轮与
   baseline 完全一致。
