# Changelog

## [1.2.2] - 2026-03-21

### Fixed
- **Critical**: Fixed `install.sh` crash (`DEFAULT_API_URL: unbound variable`) — API_URL assignment moved after region detection (`8d6920c`)
- Fixed broken Windows `set` syntax for `MANON_DIR` in skill scripts (`6694a28`)
- Eliminated phantom nodes and empty-caller edges in knowledge graph (`adf882a`)
- Scoped dao stop hook to current session via CWD match + 6h TTL (`4048625`)

### Added
- TypeScript/JS coverage support in `manon-scan-tests.py` (`fbfede0`)
- `dao-analyze.py` synced to global skill install (`254a850`)

### Improved
- Scan performance: mtime+size fast path skips unchanged files; partial parse on syntax errors (`96b58f0`)

### Docs
- Updated SKILL.md with ANALYZER/COMMITTER scripts and execution flow (`e147f97`)
- Added comment for custom tree-sitter-typescript fork (`beafd15`)

## [1.0.0] - 2026-03-16

### Changed
- **BREAKING**: Removed the legacy `shared/` package; server/client runtime code now lives under `core/`
- **BREAKING**: Renamed the local MCP package from `mcp/` to `manon_mcp/` to eliminate package-name conflicts
- Split query orchestration into `application/` services and reduced router/tool-layer business logic

### Improved
- Simplified MCP startup and registration by removing dynamic sibling/tool loading
- Added local runtime path management for SaaS state under `.manon_runtime/saas`
- Unified release version to `1.0.0` across MCP, SaaS, installers, and deployment scripts
- Updated `r760` deployment packaging to include `application/`, `core/`, and embedded `codeindex/`

### Fixed
- Fixed local impact analysis compatibility with `line_start` / `line_end` symbol fields
- Restored compatibility progress helpers in MCP sync workflows
- Kept end-to-end MCP init/scan/upload/query flow working after the architecture refactor

## [0.2.2] - 2026-03-07

### Changed
- **BREAKING**: Embedded codeindex into the repository `codeindex/` package
- Removed external codeindex dependency from requirements.txt
- All imports should now use `codeindex.*`

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
