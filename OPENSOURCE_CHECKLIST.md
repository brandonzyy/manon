# Manon 开源准备清单

## ✅ 已符合开源标准

1. **敏感信息管理**
   - ✅ API keys 通过环境变量管理
   - ✅ Secrets 通过环境变量管理
   - ✅ 没有硬编码的密钥或密码

2. **代码质量**
   - ✅ 代码结构清晰
   - ✅ 有基本文档
   - ✅ 代码健康度 86/100 (A)

## ⚠️ 需要修改的问题

### 1. 内部服务依赖

**问题文件：**
- `saas/config.py:19` - `llm_api_url: str = "https://api.matrixone.online/v1/chat/completions"`
- `mcp/_config.py:43` - `API_URL_CN = "http://saas.matrixone.online:3700"`
- `install.sh:14` - `DEFAULT_API_URL="http://saas.matrixone.online:3700"`
- `install.bat:23` - `DEFAULT_API_URL = "http://saas.matrixone.online:3700"`
- `mcp/hooks/post_push.py:61` - `return url or "http://saas.matrixone.online:3700"`

**建议修改：**
```python
# 改为可配置，提供本地部署示例
llm_api_url: str = "http://localhost:8000/v1/chat/completions"  # 示例：使用 Ollama 或其他本地 LLM
API_URL_CN = "http://localhost:3700"  # 本地部署的 saas 服务
```

### 2. 需要添加的文档

**必需文档：**
- [ ] `docs/DEPLOYMENT.md` - 部署指南
  - 如何部署 saas 服务
  - 如何配置 LLM API（支持 OpenAI、Ollama 等）
  - 环境变量配置说明

- [ ] `docs/ARCHITECTURE.md` - 架构说明
  - 各模块职责
  - 服务间通信
  - 数据流向

- [ ] `LICENSE` - 开源协议
  - 建议：MIT 或 Apache 2.0

- [ ] `CONTRIBUTING.md` - 贡献指南

### 3. 配置文件示例

**需要添加：**
- [ ] `.env.example` - 环境变量示例
  ```
  SAAS_LLM_API_KEY=your_api_key_here
  SAAS_ADMIN_SECRET=your_admin_secret
  SAAS_LLM_API_URL=http://localhost:8000/v1/chat/completions
  MANON_API_URL=http://localhost:3700
  ```

### 4. Docker 支持

**建议添加：**
- [ ] `Dockerfile` - 容器化部署
- [ ] `docker-compose.yml` - 一键启动所有服务
- [ ] `docs/DOCKER.md` - Docker 部署指南

## 📋 开源检查清单

### 代码清理
- [ ] 移除所有内部服务地址的硬编码
- [ ] 确保所有配置都可通过环境变量覆盖
- [ ] 移除或脱敏内部文档（`docs/_*.md` 已在 .gitignore）

### 文档完善
- [ ] 添加 LICENSE 文件
- [ ] 完善 README（添加部署说明）
- [ ] 添加 DEPLOYMENT.md
- [ ] 添加 ARCHITECTURE.md
- [ ] 添加 CONTRIBUTING.md

### 配置示例
- [ ] 添加 .env.example
- [ ] 更新 manon.yaml.example
- [ ] 添加 Docker 支持

### 测试验证
- [ ] 在干净环境中测试部署流程
- [ ] 验证所有配置项都有文档说明
- [ ] 确保没有遗漏的敏感信息

## 🎯 推荐的开源策略

### 方案 1：完全开源（推荐）
- 开源所有代码
- 提供完整的部署文档
- 用户可以自己部署所有服务
- 优点：社区贡献、透明度高
- 缺点：需要维护文档和社区

### 方案 2：核心开源 + SaaS 服务
- 开源 MCP 客户端和核心算法
- 提供官方 SaaS 服务（saas.matrixone.online）
- 用户可选择自部署或使用官方服务
- 优点：商业化路径清晰
- 缺点：需要维护两套配置

## 📝 下一步行动

1. 决定开源策略（方案1 或 方案2）
2. 修改内部服务地址为可配置
3. 添加必需文档
4. 添加 LICENSE 文件
5. 在测试环境验证部署流程
6. 创建公开仓库并推送代码
