# CodeIndex 重构方案

## 问题分析

**当前问题：**
1. 硬编码 `include: ['src/', 'lib/', 'tests/', 'examples/']` 导致很多项目的文件被忽略
2. 语言检测依赖完整扫描，但完整扫描又依赖 include 配置（循环依赖）
3. Parser 安装逻辑在外部（manon），应该内置到 codeindex

## 解决方案：两阶段架构

### 阶段 1：轻量级语言检测
- 只扫描文件扩展名，不解析 AST
- 只排除通用目录（node_modules, .git 等）
- 快速返回项目使用的语言列表

### 阶段 2：智能配置 + 深度扫描
- 根据检测到的语言自动安装 parser
- 生成智能的 include/exclude 配置
- 执行完整的 AST 解析扫描

## 文件结构

```
codeindex/
├── detector.py              # 新增：轻量级语言检测
├── parser_installer.py      # 新增：自动 parser 安装
├── config.py                # 修改：添加 load_with_auto_setup 方法
├── parser.py                # 现有：FILE_EXTENSIONS 需要添加 .mjs
└── scanner.py               # 现有：无需修改
```

## 实施步骤

### 1. 添加 .mjs 支持到 FILE_EXTENSIONS

```python
# codeindex/parser.py
FILE_EXTENSIONS = {
    '.py': 'python',
    '.php': 'php', '.phtml': 'php',
    '.java': 'java',
    '.ts': 'typescript', '.tsx': 'tsx',
    '.js': 'javascript', '.jsx': 'javascript',
    '.mjs': 'javascript',  # 新增
}
```

### 2. 创建 detector.py
见 `codeindex_refactor/detector.py`

### 3. 创建 parser_installer.py
见 `codeindex_refactor/parser_installer.py`

### 4. 修改 config.py
见 `codeindex_refactor/config_enhancement.py`

## API 变更

### 新增 API

```python
# 推荐使用的新入口
Config.load_with_auto_setup(root: Path) -> Config
```

### 向后兼容

```python
# 旧 API 保持不变
Config.load(path: Path) -> Config
```

## 使用示例

见 `codeindex_refactor/usage_examples.py`

## 测试要点

1. **多语言项目**：Python + JS + TS 混合项目
2. **非标准目录结构**：web/, app/, scripts/ 等
3. **Parser 安装**：首次运行自动安装
4. **性能**：轻量级检测应该 < 1 秒

## 迁移指南

### Manon 项目的简化

```python
# 之前（manon 中）
def _load_scan_config(local_path: str):
    config = Config.load(...)
    config.include = ["."]  # 手动覆盖
    langs = detect_languages(...)  # 手动检测
    ensure_parsers(langs)  # 手动安装
    # ... 复杂的逻辑

# 之后（使用新 codeindex）
def _load_scan_config(local_path: str):
    config = Config.load_with_auto_setup(Path(local_path))
    # codeindex 已经处理了语言检测和 parser 安装
    # manon 只需要添加 .gitignore 解析等特定逻辑
```

## 优势

1. **职责清晰**：codeindex 负责语言检测和扫描，manon 负责项目管理
2. **开箱即用**：无需手动配置，自动检测和安装
3. **向后兼容**：旧代码继续工作
4. **性能优化**：两阶段设计避免不必要的 AST 解析
