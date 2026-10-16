<div align="center">

# Manon

### 代码库的本地知识图谱

**纯本地的代码知识图谱引擎——解析、增量索引、语义查询、影响分析。无服务器、无数据库服务。**

[![gates](https://github.com/brandonzyy/manon/actions/workflows/ci.yml/badge.svg)](./.github/workflows/ci.yml)
License: BSL-1.1

</div>

## 它做什么

Manon 用 tree-sitter 把仓库解析成实体/关系图，**存在项目自己的 `.manon/graph/` 里**，
通过 `manon-kg` CLI 回答代码问题：

```bash
# 在项目根目录
~/manon/manon-kg sync                # 增量索引（SHA256 内容哈希）
~/manon/manon-kg query "折扣逻辑在哪" -j                     # 语义搜索
~/manon/manon-kg graph discountFor --direction callers      # 调用关系遍历
~/manon/manon-kg impact --commit HEAD                       # commit 影响传播
~/manon/manon-kg deep-query "改了 applyDiscount 会破坏什么？" --max-rounds 2  # LLM 多轮问答
~/manon/manon-kg health                                     # 8 维健康度评分
```

## 工作方式

```
codeindex/       — tree-sitter 解析 + 语言探测
core/            — 增量 AST 同步（SHA256）、契约对账、脚本归属分类
matrixone_graph/ — 图构建、向量索引、查询管线、CLI（manon-kg）
skills/          — 开发技能：manon / assurance
```

- **增量**：按内容哈希——只有变更文件被重新解析与嵌入，未变更文件走 mtime+size 快路径。
- **本地优先**：图谱落 `<项目>/.manon/graph/`（JSON 图 + npz 向量）。仅有的网络调用是
  embedding（硅基流动 `BAAI/bge-m3`，key 走 `MANON_EMBEDDING_API_KEY`）与 deep-query 的
  LLM（默认 verispring `deepseek-flash`，key 走 `VERISPRING_API_KEY`），全部带指数退避重试。
- **策展层**：配合编码代理维护的 `.project-map.md` 入口表——地图是任务入口缓存，图谱是符号级真源。

## 安装

```bash
git clone https://github.com/brandonzyy/manon.git ~/manon
cd ~/manon && ./install.sh          # venv + 依赖 + manon-kg 包装器
export MANON_EMBEDDING_API_KEY=sk-...   # 硅基流动
cd <你的项目> && ~/manon/manon-kg sync && ~/manon/manon-kg status
```

要求 Python 3.10+。架构与门禁见 [CLAUDE.md](CLAUDE.md)。

## 许可

BSL-1.1。
