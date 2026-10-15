# Manon 开源清单（纯本地版）

## ✅ 已符合开源标准

1. **敏感信息管理**
   - ✅ API keys 经环境变量注入（`MANON_EMBEDDING_API_KEY` / `VERISPRING_API_KEY`）
   - ✅ 仓库内无硬编码密钥；v1.x 的 SaaS 端点清单随 saas/ 删除一并移除
2. **许可**
   - ✅ BSL-1.1，LICENSE 文件在位
3. **文档与现实一致**
   - ✅ README / README_CN / CLAUDE.md 均为纯本地定位（v2.0.0 重写）
   - ✅ 过时的 ARCHITECTURE/DEPLOYMENT 已删除
4. **门禁**
   - ✅ L1 五棘轮 + CI（gates workflow）+ 分支保护
5. **安装体验**
   - ✅ `./install.sh` 一条命令完成 venv + 依赖 + manon-kg 包装器

## 纯本地改造记录（v2.0.0）

- 删除：`manon_mcp/`（MCP 服务）、`saas/`（自建后端）、R760 部署脚本、docker-compose（死文件）、
  e2e/管理类/配额类测试、过时架构文档。
- 保留：codeindex / core / matrixone_graph / skills / scripts / tests；查询四件全落 `manon-kg` CLI。
