# Manon — CLAUDE.md

## 项目概述

Manon (马浓) 是代码知识图谱引擎 + 开发 skill：把仓库解析成实体/关系图，
经 MCP 供 IDE 侧调用（语义搜索、图遍历、影响分析），数据落在自建 SaaS 后端。
公开仓，BSL-1.1。

## 项目结构

```
codeindex/       — 源码解析与语言探测（tree-sitter parser 装配）
core/            — AST 同步、契约对账（contract_audit）、脚本归属分类
manon_mcp/       — MCP 服务端（IDE 集成、Claude Code 钩子）
matrixone_graph/ — 图查询、影响分析、嵌入与健康度
saas/            — 数据服务后端（:3700）
skills/          — 随仓发布的两个 skill：manon / assurance
scripts/         — 门禁与运维执行器
tests/           — 单元与集成用例
```

Web 客户端与它的浏览器验证脚本已在 `ed6eb5f` 从公开仓移除。

## 门禁

**强制在机外**：GitHub Actions + 分支保护。`master` 与 `dev` 都要求
`l1-and-tests` 与 `secrets` 两个检查通过，且 `strict: true`（分支必须先追上
基线才能合）。`--no-verify` 绕得过本机钩子，绕不过这一层。

**本机是快反馈**，约 2 秒：

```bash
python3 scripts/install-hooks.py          # 装 pre-commit + 仓外 L1 工具链
python3 scripts/install-hooks.py --check  # 只看装没装
```

L1 工具链必须用**独立 venv**（默认 `~/.cache/manon-l1-venv`），三条理由都是实测：

- CI 刻意在装产品依赖**之前**跑 L1。用项目 `.venv` 跑同一条判据，mypy 多报
  6 条 `import-untyped`——一道红在环境差异上的门禁会被绕过。
- venv 放仓内会被 vulture 当源码扫进去，多出 40 余条 unused import。
- 裸 `python3` 跑出的红照着去 `--regenerate`，幻影条目进 baseline，CI 随即以
  「变少了」再红一次。`check_l1.py` 因此起手就查产品依赖在不在场，在就拒；
  逃生口 `MANON_L1_ALLOW_DIRTY=1` 只放行读数，对 `--regenerate` 一律无效。

`deps`（pip-audit 连 OSV）是网络判据，不进提交路径——它把钩子从 2 秒拖到 17 秒，
超出「提交钩子 ≤15 秒」的预算。它的执行器在 CI。

```bash
L1=~/.cache/manon-l1-venv/bin/python              # 解释器是判据的一部分
$L1 scripts/check_l1.py                          # 五条棘轮全跑（含网络那条）
$L1 scripts/check_l1.py lint typing dead contract
$L1 scripts/check_l1.py --regenerate             # 修好之后收紧 baseline
python3 scripts/check_skills.py                  # skills 装块覆盖 + 交叉引用
```

## R760 生产服务器

| 项目 | 值 |
|------|-----|
| 地址 | `114.94.190.2:2212` |
| 用户 | `root` |
| 认证 | key-based — `~/.ssh/id_ed25519` |
| 服务目录 | `/root/manon` |
| 服务端口 | `:3700` |

```bash
ssh -i ~/.ssh/id_ed25519 -p 2212 root@114.94.190.2

python3 scripts/deploy-r760.py          # 强制部署
python3 scripts/deploy-r760.py --auto   # 仅服务端文件变更时
```
