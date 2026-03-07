# Changelog

## [0.2.2] - 2026-03-07

### Changed
- **BREAKING**: Embedded codeindex into `mcp/codeindex/` package
- Removed external codeindex dependency from requirements.txt
- All imports changed from `codeindex.*` to `mcp.codeindex.*`

### Improved
- Fast language detection with `max_files=500` limit (0.01s vs 30s+)
- Parser installation timeout reduced to 30s with PyPI-first strategy
- Memory caching for language detection to avoid repeated scans
- Direct control over codeindex optimizations

### Fixed
- **Critical**: Fixed manon_init hanging caused by parameter mismatch with external codeindex
- No more version conflicts between Manon and external codeindex package

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
