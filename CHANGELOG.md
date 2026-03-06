# Changelog

## [0.2.1] - 2026-03-07

### Changed
- Migrated to brandonzyy/codeindex fork with enhanced language detection
- Automatic language detection now supports `.mjs` files
- Automatic tree-sitter parser installation

### Improved
- Simplified codebase by removing 70+ lines of duplicate code
- `_load_scan_config` now uses `Config.load_with_auto_setup()`
- `ensure_parsers` delegates to codeindex built-in functions

### Fixed
- Language detection now correctly identifies JavaScript/TypeScript projects

## [0.2.0] - 2026-02-23

Initial release with MCP integration and knowledge graph support.
