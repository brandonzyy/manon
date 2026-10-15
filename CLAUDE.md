# Manon — CLAUDE.md

## 项目概述

Manon (马浓) 是纯本地的代码知识图谱引擎：把仓库解析成实体/关系图，落盘到项目
`.manon/` 目录，经 `manon-kg` CLI 供会话内查询（语义搜索、图遍历、影响分析、
LLM 多轮问答）。无服务器、无数据库服务、无 SaaS 依赖。公开仓，BSL-1.1。

## 项目结构

```
codeindex/       — 源码解析与语言探测（tree-sitter parser 装配）
core/            — AST 同步、契约对账（contract_audit）、脚本归属分类
matrixone_graph/ — 图构建、查询、影响分析、嵌入、健康度、CLI（manon-kg）
skills/          — 随仓发布的 skill：manon / assurance
scripts/         — 门禁与工具执行器
tests/           — 单元与集成用例
```

## manon-kg CLI

入口：`<仓根>/manon-kg`（包装 venv 解释器，任意 cwd 可用）。在项目根目录运行：

```bash
~/manon/manon-kg sync          # 增量索引（SHA256 内容增量）→ <项目>/.manon/graph/
~/manon/manon-kg query "..."   # 语义搜索（需 embedding key）
~/manon/manon-kg deep-query "..." --max-rounds 2   # LLM 多轮图谱问答
~/manon/manon-kg graph SYM --direction both --depth 1   # 符号调用关系遍历
~/manon/manon-kg impact --commit HEAD                  # commit 级影响传播
~/manon/manon-kg health                                # 8 维健康度
~/manon/manon-kg status | clear
```

embedding：硬编码硅基流动 `BAAI/bge-m3`；key 经 `MANON_EMBEDDING_API_KEY` 注入
（sync/query 必需）。deep-query 的 LLM 硬编码 verispring `deepseek-flash`，key 经
`VERISPRING_API_KEY` 注入。所有网络调用带 4 次指数退避重试。

## 门禁

**强制在机外**：GitHub Actions + 分支保护。`master` 与 `dev` 都要求
`l1-and-tests` 与 `secrets` 两个检查通过，且 `strict: true`。`--no-verify`
绕得过本机钩子，绕不过这一层。

**本机是快反馈**，约 2 秒：

```bash
python3 scripts/install-hooks.py          # 装 pre-commit + 仓外 L1 工具链
python3 scripts/install-hooks.py --check  # 只看装没装
```

L1 工具链必须用**独立 venv**（默认 `~/.cache/manon-l1-venv`）：CI 刻意在装产品
依赖之前跑 L1，产品依赖在场时 mypy 会解析 numpy 的 PEP 695 语法在 3.10 语义下
fatal。`check_l1.py` 按 `sys.executable` 同级 bin 解析工具，与
`scripts/requirements-l1.txt` 钉的版本逐个核对。`deps`（pip-audit 连 OSV）是网络
判据，不进提交路径（执行器在 CI）。

```bash
L1=~/.cache/manon-l1-venv/bin/python              # 工具链是判据的一部分
$L1 scripts/check_l1.py                          # 五条棘轮全跑
$L1 scripts/check_l1.py lint typing dead contract
$L1 scripts/check_l1.py --regenerate             # 修好之后收紧 baseline
python3 scripts/check_skills.py                  # skills 装块覆盖 + 交叉引用
```

## 历史注记

- v1.x 时代的 MCP 服务（`manon_mcp/`）与自建 SaaS 后端（`saas/`，R760 部署）已在
  v2.0.0 移除，git 历史（tag v1.7.1 及之前）保留全部实现与部署记录。
- Web 客户端在 `ed6eb5f` 移除。
