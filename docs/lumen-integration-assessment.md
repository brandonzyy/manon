# Lumen 整合进 Manon 的可行性评估

**评估日期**: 2025-03-08
**评估对象**: Lumen (文档知识图谱) → Manon (代码知识图谱)
**评估方法**: 基于 Manon 知识图谱深度分析

---

## 1. 项目概况

### Lumen 架构特点
- **定位**: 文档知识图谱引擎，为 Markdown 文档提供语义索引和搜索
- **核心模块**:
  - `matrixdocgraph`: 图谱存储引擎 (Entity/Relation/Chunk + NetworkX + numpy)
  - `doc_parser`: Markdown 文档解析器
  - `lumen.py`: 主引擎，封装图谱构建和查询
- **存储方式**: 本地文件系统 (NetworkX JSON + numpy .npz)
- **向量索引**: numpy 数组 + 余弦相似度
- **API 接口**: FastAPI (REST) + MCP 工具

### Manon 架构特点
- **定位**: 代码知识图谱引擎，为源代码提供精确上下文
- **核心模块**:
  - `matrixone_graph`: MatrixOne 数据库存储引擎
- `core/ast`: Tree-sitter AST 解析器
  - `saas/services`: 图谱构建和查询服务
- **存储方式**: MatrixOne 分布式数据库 (云端)
- **向量索引**: MatrixOne 向量索引 + 混合检索
- **API 接口**: FastAPI (REST) + MCP 工具

---

## 2. 核心差异分析

### 2.1 存储层差异

| 维度 | Lumen | Manon |
|------|-------|-------|
| **图谱存储** | NetworkX DiGraph (内存 + JSON 持久化) | MatrixOne 数据库 (分布式) |
| **向量存储** | numpy 数组 (.npz 文件) | MatrixOne 向量索引 |
| **数据规模** | 小规模 (单个文档库，600 实体) | 大规模 (多项目，10K+ 实体) |
| **并发性能** | 单进程，无并发控制 | 数据库级并发，支持多租户 |
| **持久化** | 文件系统 (.lumen/ 目录) | 云端数据库 |

**关键发现**:
- Lumen 的 `CodeGraph` 和 `VectorIndex` 是轻量级内存结构，适合单用户场景
- Manon 的 MatrixOne 存储支持多租户、增量更新、分布式查询
- **架构不兼容**: Lumen 的存储层无法直接替换为 MatrixOne

### 2.2 数据模型差异

| 维度 | Lumen | Manon |
|------|-------|-------|
| **实体类型** | `Entity(id, name, kind, file_path, line_start, line_end, metadata)` | `Entity(id, name, kind, file_path, line_start, line_end, signature, docstring)` |
| **关系类型** | `Relation(source, target, kind)` | `Relation(source, target, kind, metadata)` + 调用链路 |
| **块类型** | `Chunk(id, entity_id, content, line_start, line_end)` | `Chunk(id, entity_id, content, hash, embedding)` |
| **元数据** | 文档级 (manifest.json) | 代码级 (AST 节点、类型信息) |

**关键发现**:
- 数据模型高度相似，都是 Entity-Relation-Chunk 三元组
- Lumen 侧重文档结构 (章节、段落)，Manon 侧重代码结构 (函数、类、调用)
- **可复用**: 数据模型设计理念一致，可以统一抽象

### 2.3 解析器差异

| 维度 | Lumen | Manon |
|------|-------|-------|
| **输入格式** | Markdown 文档 | 源代码 (Python/JS/TS/Go/Rust...) |
| **解析器** | `doc_parser.markdown.parse_markdown` | Tree-sitter (多语言 AST) |
| **提取内容** | 标题、段落、代码块 | 函数、类、调用关系、导入 |
| **关系构建** | 文档间引用 (manifest.json) | 调用图、依赖图 |

**关键发现**:
- 解析器完全不同，无法复用
- Lumen 的文档解析逻辑简单 (正则 + Markdown 结构)
- Manon 的 AST 解析复杂 (Tree-sitter + 语言特定逻辑)
- **不可复用**: 解析器需要独立维护

### 2.4 查询接口差异

| 维度 | Lumen | Manon |
|------|-------|-------|
| **语义搜索** | `lumen.search(query, top_k, depth)` | `manon_search(query, top_k, depth)` |
| **图遍历** | 无 (仅支持关系扩展) | `manon_graph(symbol, direction, depth)` |
| **影响分析** | 无 | `manon_impact(commit)` |
| **深度查询** | 无 | `manon_deep_query(question, max_rounds)` |
| **代码健康** | 无 | `manon_code_health()` |

**关键发现**:
- Lumen 功能单一 (语义搜索 + 统计)
- Manon 功能丰富 (搜索 + 图遍历 + 影响分析 + LLM 推理)
- **可扩展**: Lumen 的查询接口可以作为 Manon 的子集

---

## 3. 整合方案评估

### 方案 A: 完全整合 (统一存储层)

**思路**: 将 Lumen 的 `matrixdocgraph` 替换为 Manon 的 MatrixOne 存储

**优点**:
- 统一存储，减少维护成本
- 支持文档 + 代码混合查询
- 利用 MatrixOne 的分布式能力

**缺点**:
- **工作量巨大**: 需要重写 Lumen 的所有存储逻辑
- **性能损失**: 文档查询不需要分布式数据库，本地存储更快
- **复杂度增加**: MatrixOne 的部署和运维成本高

**结论**: ❌ **不推荐** — 收益不足以抵消成本

---

### 方案 B: 模块化整合 (共享 MCP 接口)

**思路**: 保持 Lumen 和 Manon 独立，通过统一的 MCP 接口层整合

**实现**:
1. 创建 `manon-unified` MCP 服务
2. 注册两类工具:
   - `manon_*` 工具 → 调用 Manon SaaS API (代码图谱)
   - `lumen_*` 工具 → 调用 Lumen 本地引擎 (文档图谱)
3. 提供跨图谱查询工具:
   - `unified_search(query, scope="code|docs|all")` → 同时查询代码和文档
   - `unified_context(symbol)` → 返回代码定义 + 相关文档

**优点**:
- ✅ **低成本**: 无需修改现有架构，仅需 MCP 层适配
- ✅ **灵活性**: 用户可以选择只用代码图谱或同时用文档图谱
- ✅ **渐进式**: 可以先实现基础整合，再逐步增强

**缺点**:
- 需要维护两套存储和查询逻辑
- 跨图谱查询性能可能不如单一存储

**结论**: ✅ **推荐** — 平衡了成本和收益

---

### 方案 C: 轻量级整合 (文档作为代码注释)

**思路**: 将 Markdown 文档解析为特殊的 "文档实体"，存入 Manon 图谱

**实现**:
1. 扩展 Manon 的 AST 解析器，支持 Markdown 文件
2. 将文档章节映射为 `Entity(kind="doc_section")`
3. 建立文档与代码的关联关系:
   - 文档中的代码引用 → `Relation(kind="doc_references_code")`
   - 代码中的文档链接 → `Relation(kind="code_references_doc")`
4. 在 `manon_search` 中同时检索代码和文档实体

**优点**:
- ✅ **统一存储**: 文档和代码在同一个图谱中
- ✅ **关系明确**: 可以追踪文档-代码的双向引用
- ✅ **查询简单**: 无需跨图谱查询

**缺点**:
- Markdown 解析逻辑需要集成到 Manon
- 文档实体可能稀释代码图谱的精度
- 不适合大规模文档库 (会显著增加图谱规模)

**结论**: ⚠️ **有条件推荐** — 适合文档量较小的项目

---

## 4. 推荐方案: 模块化整合 (方案 B)

### 4.1 架构设计

```
┌─────────────────────────────────────────────────────────┐
│  MCP Client (Claude Code / Cursor / Windsurf)           │
└────────────────────┬────────────────────────────────────┘
                     │
         ┌───────────▼───────────┐
         │  manon-unified MCP    │
         │  (统一工具注册层)      │
         └───────┬───────┬───────┘
                 │       │
        ┌────────▼──┐ ┌──▼────────┐
        │  Manon    │ │  Lumen    │
        │  (代码)   │ │  (文档)   │
        └────┬──────┘ └──────┬────┘
             │               │
    ┌────────▼────────┐ ┌───▼──────────┐
    │ MatrixOne 数据库│ │ 本地文件系统  │
    │ (云端图谱)      │ │ (.lumen/)    │
    └─────────────────┘ └──────────────┘
```

### 4.2 工具列表

#### 代码图谱工具 (Manon)
- `manon_init` — 初始化代码项目
- `manon_search` — 语义搜索代码
- `manon_graph` — 调用图遍历
- `manon_impact` — 提交影响分析
- `manon_deep_query` — 深度代码分析
- `manon_code_health` — 代码健康度

#### 文档图谱工具 (Lumen)
- `lumen_init` — 初始化文档库
- `lumen_search` — 语义搜索文档
- `lumen_stats` — 文档图谱统计

#### 跨图谱工具 (新增)
- `unified_search(query, scope)` — 同时搜索代码和文档
- `unified_context(symbol)` — 获取代码定义 + 相关文档
- `unified_explain(concept)` — 结合代码实现和文档说明

### 4.3 实现步骤

**Phase 1: 基础整合 (1-2 周)**
1. 创建 `manon-unified` MCP 服务
2. 注册 `manon_*` 和 `lumen_*` 工具
3. 实现工具路由逻辑 (根据前缀分发请求)
4. 更新 install.sh，支持同时安装 Manon 和 Lumen

**Phase 2: 跨图谱查询 (2-3 周)**
1. 实现 `unified_search` — 并行查询两个图谱，合并结果
2. 实现 `unified_context` — 根据代码符号查找相关文档
3. 添加文档-代码关联规则 (如文件名匹配、显式引用)

**Phase 3: 智能推理 (3-4 周)**
1. 实现 `unified_explain` — 结合 LLM 生成代码+文档的综合解释
2. 优化跨图谱查询性能 (缓存、预加载)
3. 添加配置选项 (用户可选择启用/禁用文档图谱)

### 4.4 技术挑战

1. **MCP 工具命名冲突**
   - 解决方案: 使用前缀区分 (`manon_*` vs `lumen_*`)

2. **两套存储的同步问题**
   - 解决方案: 不强制同步，由用户手动触发 `lumen_init` 和 `manon_init`

3. **跨图谱查询的性能**
   - 解决方案: 并行查询 + 结果缓存

4. **文档-代码关联的准确性**
   - 解决方案: 启发式规则 (文件名、路径、显式引用) + LLM 辅助

---

## 5. 成本收益分析

### 开发成本
- **Phase 1**: 1-2 周 (基础整合)
- **Phase 2**: 2-3 周 (跨图谱查询)
- **Phase 3**: 3-4 周 (智能推理)
- **总计**: 6-9 周

### 预期收益
1. **用户体验提升**: 一次查询同时获取代码和文档上下文
2. **功能差异化**: 市面上没有代码+文档混合图谱的产品
3. **生态扩展**: 为未来支持更多文档类型 (API 文档、设计文档) 打基础

### 风险评估
- **低风险**: 模块化设计，不影响现有 Manon 功能
- **中风险**: 跨图谱查询的性能和准确性需要实际测试
- **高风险**: 用户可能不需要文档图谱功能 (需求验证)

---

## 6. 结论与建议

### 核心结论
✅ **推荐整合 Lumen 到 Manon**，采用 **模块化整合方案 (方案 B)**

### 理由
1. **技术可行**: 两者架构独立，MCP 层整合成本低
2. **功能互补**: 代码图谱 + 文档图谱 = 完整的项目上下文
3. **风险可控**: 不影响现有功能，可渐进式推进

### 下一步行动
1. **需求验证**: 调研用户是否需要文档图谱功能
2. **原型开发**: 实现 Phase 1 (基础整合)，验证技术方案
3. **性能测试**: 测试跨图谱查询的性能和准确性
4. **用户反馈**: 小范围发布，收集反馈后决定是否继续 Phase 2/3

---

**评估人**: Claude Opus 4.6 (基于 Manon 知识图谱分析)
**评估工具**: `manon_deep_query`, `manon_search`, `manon_code_health`
**图谱统计**: Lumen (600 实体, 827 关系) | Manon (52K+ 实体, 73K+ 关系)
