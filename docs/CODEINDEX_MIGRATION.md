# CodeIndex 内置迁移指南

## 背景

从 v0.2.2 开始，Manon 将 codeindex 从外部依赖改为内置包（`mcp/codeindex/`）。

## 为什么内置？

1. **避免版本冲突**：外部 codeindex 更新可能导致参数不匹配，引发 MCP 工具卡住
2. **直接优化**：可以直接修改代码进行性能优化，无需等待上游合并
3. **简化依赖**：减少外部依赖管理的复杂度
4. **更好的控制**：完全掌控代码质量和性能

## 改动内容

### 1. 目录结构

```
manon/
├── mcp/
│   ├── codeindex/          # 新增：内置 codeindex
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── scanner.py
│   │   ├── parser.py
│   │   ├── detector.py     # 优化版：max_files=500
│   │   ├── parser_installer.py  # 优化版：timeout=30s
│   │   └── parsers/
│   ├── _tools.py
│   └── ...
├── shared/
│   └── ast_sync.py
└── requirements.txt        # 移除 codeindex 依赖
```

### 2. 导入路径变更

**旧代码**：
```python
from codeindex.detector import quick_detect_languages
from codeindex.parser import parse_file
from codeindex.scanner import scan_directory
```

**新代码**：
```python
from mcp.codeindex.detector import quick_detect_languages
from mcp.codeindex.parser import parse_file
from mcp.codeindex.scanner import scan_directory
```

### 3. 优化内容

#### detector.py
- 新增 `max_files` 参数（默认 500）
- 新增 `max_depth` 限制（5 层）
- 性能提升：0.01s vs 30s+（大型项目）

#### parser_installer.py
- 超时从 60s 降至 30s
- PyPI 优先策略（国内镜像作为备选）
- 更好的错误处理

#### ast_sync.py
- 新增内存缓存 `_LANG_CACHE`
- 避免重复扫描同一项目

## 升级步骤

### 对于 Manon 开发者

1. **拉取最新代码**：
   ```bash
   cd ~/Desktop/matrixone/infrastructure/manon
   git pull
   ```

2. **重新安装依赖**（可选，如果遇到问题）：
   ```bash
   pip uninstall codeindex -y
   pip install -r requirements.txt
   ```

3. **重启 MCP 服务器**：
   - 重启 Claude Code 或
   - 手动重启 MCP 进程

### 对于使用 Manon 的项目

无需任何改动，MCP 工具的 API 保持不变。

## 验证

测试内置 codeindex 是否正常工作：

```bash
cd ~/Desktop/matrixone/infrastructure/manon
python -c "
from mcp.codeindex.detector import quick_detect_languages
from mcp.codeindex.parser import FILE_EXTENSIONS
from pathlib import Path
result = quick_detect_languages(Path('.'), FILE_EXTENSIONS, max_files=500)
print(f'Detected languages: {result}')
"
```

预期输出：
```
Detected languages: {'python', 'javascript'}
```

## 故障排除

### 问题：导入错误 "No module named 'mcp.codeindex'"

**原因**：MCP 服务器未重启，仍在使用旧代码

**解决**：
1. 重启 Claude Code
2. 或检查 `~/.manon/mcp.log` 确认服务器版本

### 问题：manon_init 仍然卡住

**原因**：可能是其他步骤超时（如 API 调用）

**解决**：
1. 检查 `~/.manon/mcp.log` 查看卡在哪一步
2. 测试 API 连通性：`curl http://saas.matrixone.online:3700/health`

## 未来计划

- [ ] 进一步优化 `scan_directory` 性能
- [ ] 添加增量扫描支持
- [ ] 支持更多编程语言
- [ ] 改进错误提示和日志

## 参考

- 原始 codeindex: https://github.com/brandonzyy/codeindex
- 内置版本: `mcp/codeindex/`
- 提交记录: bb5cdaf
