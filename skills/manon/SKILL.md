---
name: manon
description: manon 本地图谱的维护与查询入口（sync / query / graph / impact / deep-query / health）
user_invocable: true
---

# Manon 本地图谱

项目根目录下通过 `~/manon/manon-kg` CLI 使用（存储在 `<项目>/.manon/graph/`）。

**核心规则**：
- 每次任务后必须 `~/manon/manon-kg sync`，把结构变化并入图谱（增量，SHA256 判定）。
- 定位代码先 `~/manon/manon-kg query "关键词" -j`，再按命中 `路径:行号` 截读，不通读整文件。
- 深层理解用 `~/manon/manon-kg deep-query "问题" --max-rounds 2 -j`（LLM 多轮规划）。
- 改公共函数/跨模块前，先 `~/manon/manon-kg graph 符号名 --direction callers` 看调用方，
  或 `~/manon/manon-kg impact --commit HEAD` 看影响传播。
- 工具语义与适用边界见《Manon 调用规则》（`~/.claude/specs/Manon调用规则.md`）。
