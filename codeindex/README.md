# Embedded CodeIndex

This directory contains an embedded version of codeindex, optimized for Manon's use cases.

## Why Embedded?

1. **Version Control**: Avoid dependency version conflicts
2. **Direct Optimization**: Easy to modify and optimize for Manon's needs
3. **Reduced Complexity**: No external dependency management
4. **Performance**: Optimized for MCP tool usage patterns

## Key Optimizations

- `detector.py`: Fast language detection with `max_files` limit (500 files, 5 depth max)
- `parser_installer.py`: Reduced timeout (30s), PyPI-first strategy
- Memory caching in `core.ast` to avoid repeated scans

## Modules

- `config.py`: Configuration management
- `scanner.py`: Directory scanning and file filtering
- `parser.py`: AST parsing with tree-sitter
- `detector.py`: Fast language detection
- `parser_installer.py`: Tree-sitter parser installation
- `parsers/`: Language-specific parsers

## Usage

```python
from codeindex.detector import quick_detect_languages
from codeindex.parser import parse_file, FILE_EXTENSIONS
from codeindex.scanner import scan_directory
```

## Original Source

Based on: https://github.com/brandonzyy/codeindex
Embedded on: 2026-03-07
