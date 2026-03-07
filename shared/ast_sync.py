"""Local AST extraction + incremental sync to saas/ backend.

Ported from manon-mcp/server.py — shares the same ~/.manon/projects.json
cache so MCP and web clients see consistent file hashes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("manon.ast_sync")

SYNC_BATCH_SIZE = 50
PROJECTS_DIR = Path.home() / ".manon"
PROJECTS_FILE = PROJECTS_DIR / "projects.json"

# Universal directories to always exclude — covers all major languages/frameworks.
# .gitignore is also parsed automatically for project-specific patterns.
_ALWAYS_EXCLUDE = [
    # Version control
    "**/.git/**", "**/.svn/**", "**/.hg/**",
    # Python
    "**/.venv/**", "**/venv/**", "**/__pycache__/**",
    "**/*.egg-info/**", "**/.tox/**", "**/.nox/**",
    "**/.mypy_cache/**", "**/.pytest_cache/**", "**/.ruff_cache/**",
    # Node / JS / TS
    "**/node_modules/**", "**/.yarn/**", "**/.pnpm-store/**",
    "**/bower_components/**",
    # Build outputs
    "**/dist/**", "**/build/**", "**/out/**", "**/target/**",
    "**/_build/**", "**/.next/**", "**/.nuxt/**", "**/.output/**",
    "**/.svelte-kit/**", "**/.turbo/**",
    # Java / JVM
    "**/.gradle/**", "**/.m2/**",
    # IDE / Editor
    "**/.idea/**", "**/.vscode/**", "**/.vs/**", "**/.eclipse/**",
    # Coverage / test artifacts
    "**/htmlcov/**", "**/.nyc_output/**", "**/coverage/**",
    # Misc
    "**/.cache/**", "**/.tmp/**", "**/.temp/**",
]


# ── Test framework auto-detection ─────────────────────

def detect_test_patterns(root: Path) -> tuple[list[str], list[str]]:
    """Auto-detect test frameworks and return exclusion patterns.

    Phase A: config file detection (root + one-level subdirs)
    Phase B: directory name convention detection

    Returns (deduplicated_patterns, report_lines like ["pytest: test_*.py, tests/"]).
    """
    root = root.resolve()
    patterns: set[str] = set()
    frameworks: dict[str, set[str]] = {}  # framework_name -> display hints

    # ── Phase A: config file detection ──

    # Helper: check if a file exists in root or one-level subdirs
    def _exists(name: str) -> bool:
        if (root / name).exists():
            return True
        try:
            for d in root.iterdir():
                if d.is_dir() and (d / name).exists():
                    return True
        except OSError:
            pass
        return False

    def _file_contains(name: str, needle: str) -> bool:
        """Check if a file in root contains a string (shallow check)."""
        p = root / name
        if not p.exists():
            return False
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:8192]
            return needle in text
        except Exception:
            return False

    # pytest
    if (_exists("conftest.py") or _exists("pytest.ini")
            or _file_contains("pyproject.toml", "[tool.pytest")
            or _file_contains("setup.cfg", "[tool:pytest")):
        _pytest_pats = ["**/test_*.py", "**/*_test.py", "**/conftest.py", "**/tests/**"]
        patterns.update(_pytest_pats)
        frameworks["pytest"] = {"test_*.py", "*_test.py", "tests/"}

    # Jest
    _has_jest_config = any(_exists(f"jest.config.{ext}") for ext in ("js", "ts", "mjs", "cjs", "json"))
    if _has_jest_config or _file_contains("package.json", '"jest"'):
        _jest_pats = [
            "**/__tests__/**",
            "**/*.test.ts", "**/*.test.tsx", "**/*.test.js", "**/*.test.jsx",
            "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.js", "**/*.spec.jsx",
        ]
        patterns.update(_jest_pats)
        frameworks["jest"] = {"__tests__/", "*.test.{ts,js}"}

    # Vitest
    _has_vitest_config = any(_exists(f"vitest.config.{ext}") for ext in ("js", "ts", "mjs", "cjs"))
    if _has_vitest_config or _file_contains("package.json", '"vitest"'):
        _vitest_pats = [
            "**/__tests__/**",
            "**/*.test.ts", "**/*.test.tsx", "**/*.test.js", "**/*.test.jsx",
            "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.js", "**/*.spec.jsx",
        ]
        patterns.update(_vitest_pats)
        frameworks["vitest"] = {"__tests__/", "*.test.{ts,js}"}

    # Cypress
    _has_cypress_config = any(_exists(f"cypress.config.{ext}") for ext in ("js", "ts", "mjs", "cjs"))
    if _has_cypress_config or (root / "cypress").is_dir():
        _cypress_pats = ["**/cypress/**", "**/*.cy.ts", "**/*.cy.js"]
        patterns.update(_cypress_pats)
        frameworks["cypress"] = {"cypress/", "*.cy.{ts,js}"}

    # Playwright
    _has_pw_config = any(_exists(f"playwright.config.{ext}") for ext in ("js", "ts", "mjs", "cjs"))
    if _has_pw_config:
        _pw_pats = ["**/*.spec.ts", "**/*.spec.js", "**/e2e/**"]
        patterns.update(_pw_pats)
        frameworks["playwright"] = {"*.spec.{ts,js}", "e2e/"}

    # Go test
    try:
        has_go_test = any(root.glob("*_test.go")) or any(root.glob("**/*_test.go"))
    except OSError:
        has_go_test = False
    if has_go_test or _exists("go.mod"):
        # Only add if go test files actually exist
        try:
            if any(root.rglob("*_test.go")):
                patterns.add("**/*_test.go")
                frameworks["go test"] = {"*_test.go"}
        except OSError:
            pass

    # Java (Maven/Gradle convention)
    if (root / "src" / "test").is_dir():
        patterns.add("**/src/test/**")
        frameworks["java/maven"] = {"src/test/"}

    # Rust
    if _exists("Cargo.toml") and (root / "tests").is_dir():
        patterns.add("**/tests/**")
        frameworks["rust"] = {"tests/"}

    # ── Phase B: directory name conventions (supplement) ──
    _test_dirs = {
        "tests": "**/tests/**",
        "test": "**/test/**",
        "__tests__": "**/__tests__/**",
        "spec": "**/spec/**",
        "e2e": "**/e2e/**",
        "cypress": "**/cypress/**",
    }
    for dirname, pat in _test_dirs.items():
        if (root / dirname).is_dir() and pat not in patterns:
            patterns.add(pat)
            if dirname not in frameworks:
                frameworks.setdefault("目录约定", set()).add(f"{dirname}/")

    # Build report lines
    report: list[str] = []
    for fw, hints in sorted(frameworks.items()):
        report.append(f"{fw}: {', '.join(sorted(hints))}")

    return sorted(patterns), report


# ── Project registry (shared with MCP) ──────────────

def load_projects() -> dict:
    if PROJECTS_FILE.exists():
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    return {"projects": {}}


def save_projects(data: dict) -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_project(local_path: str) -> dict | None:
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    return load_projects()["projects"].get(norm)


def set_project(local_path: str, info: dict) -> None:
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    data = load_projects()
    data["projects"][norm] = info
    save_projects(data)


def find_project_by_repo_id(repo_id: str) -> tuple[str, dict] | None:
    for path, info in load_projects()["projects"].items():
        if info.get("repo_id") == repo_id:
            return path, info
    return None


# ── Config loading with .gitignore support ────────────

def _load_scan_config(local_path: str):
    """Load codeindex Config with auto language detection and augment with .gitignore + custom excludes + test patterns.

    Returns (config, root, test_excludes) where test_excludes is the list of
    auto-detected test framework exclusion patterns.
    """
    from codeindex.config import Config

    root = Path(local_path).resolve()

    # Use new API: auto-detects languages, installs parsers, generates smart config
    config = Config.load_with_auto_setup(root)

    # Merge: existing excludes + always-exclude + .gitignore + test auto-detect + custom
    excludes = set(config.exclude)
    excludes.update(_ALWAYS_EXCLUDE)

    # Parse .gitignore
    gitignore = root / ".gitignore"
    if gitignore.exists():
        for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            pattern = line.rstrip("/")
            if "/" not in pattern:
                excludes.add(f"**/{pattern}/**")
                excludes.add(f"**/{pattern}")
            else:
                excludes.add(f"**/{pattern}/**")
                excludes.add(f"**/{pattern}")

    # Auto-detect test frameworks
    test_excludes, _test_report = detect_test_patterns(root)
    excludes.update(test_excludes)

    # Load custom excludes from project registry
    proj = get_project(local_path)
    if proj:
        for pat in proj.get("custom_excludes", []):
            excludes.add(pat)

    config.exclude = list(excludes)
    return config, root, test_excludes


def preview_project_structure(local_path: str) -> str:
    """List top-level directory structure with file counts for LLM review.

    Returns a formatted string showing each top-level directory, its file count,
    and whether it's currently excluded. The calling LLM uses this to decide
    if additional exclusions are needed.
    """
    root = Path(local_path).resolve()
    config, _, _test_exc = _load_scan_config(local_path)
    from codeindex.scanner import should_exclude

    lines = []
    try:
        items = sorted(root.iterdir())
    except OSError:
        return "无法读取目录"

    for item in items:
        if not item.is_dir():
            continue
        name = item.name
        excluded = should_exclude(item, config.exclude, root)
        # Quick file count (non-recursive, cap at 100 to stay fast)
        try:
            count = sum(1 for _ in zip(range(500), item.rglob("*")) if True)
            count_str = f"{count}+" if count >= 500 else str(count)
        except OSError:
            count_str = "?"
        marker = "✗ 已排除" if excluded else "○ 待扫描"
        lines.append(f"  {marker}  {name}/  ({count_str} 文件)")

    return "\n".join(lines) if lines else "（空目录）"


def analyze_index_coverage(local_path: str, indexed_hashes: dict[str, str]) -> str:
    """Analyze index coverage: scannable vs indexed files per top-level directory.

    Returns a formatted table with per-directory status, exclusion reasons,
    and a summary line.
    """
    from codeindex.scanner import scan_directory, should_exclude

    root = Path(local_path).resolve()
    config, _, test_excludes = _load_scan_config(local_path)

    # Get all scannable files
    scan_result = scan_directory(root, config, root)
    scannable_by_dir: dict[str, int] = {}
    for f in scan_result.files:
        rel = str(f.relative_to(root)).replace("\\", "/")
        top_dir = rel.split("/")[0] if "/" in rel else "."
        scannable_by_dir[top_dir] = scannable_by_dir.get(top_dir, 0) + 1

    # Count indexed files per directory
    indexed_by_dir: dict[str, int] = {}
    for rel_path in indexed_hashes:
        top_dir = rel_path.split("/")[0] if "/" in rel_path else "."
        indexed_by_dir[top_dir] = indexed_by_dir.get(top_dir, 0) + 1

    # Gather all top-level directories
    try:
        all_dirs = sorted(d.name for d in root.iterdir() if d.is_dir())
    except OSError:
        return "  无法读取目录"

    # Collect exclude patterns for display
    proj = get_project(local_path)
    custom_excludes = proj.get("custom_excludes", []) if proj else []

    # Build rows: (icon, name, coverage, reason)
    indexed_rows: list[tuple[str, str, str, str]] = []
    excluded_rows: list[tuple[str, str, str, str]] = []
    skipped_rows: list[tuple[str, str, str, str]] = []

    for d in all_dirs:
        scannable = scannable_by_dir.get(d, 0)
        indexed = indexed_by_dir.get(d, 0)
        dir_path = root / d

        if should_exclude(dir_path, config.exclude, root):
            pattern, source = _find_exclude_reason(d, config.exclude, custom_excludes, test_excludes)
            excluded_rows.append(("✗", f"{d}/", "排除", f"{pattern} ({source})"))
        elif scannable == 0:
            skipped_rows.append(("─", f"{d}/", "跳过", "无源码文件"))
        elif indexed >= scannable:
            indexed_rows.append(("✅", f"{d}/", f"{indexed}/{scannable}", ""))
        elif indexed > 0:
            indexed_rows.append(("🟡", f"{d}/", f"{indexed}/{scannable}", "部分同步"))
        else:
            indexed_rows.append(("○", f"{d}/", f"0/{scannable}", "待同步"))

    if not indexed_rows and not excluded_rows and not skipped_rows:
        return ""

    # Collect supported extensions
    exts = set()
    for f in scan_result.files:
        suffix = f.suffix
        if suffix:
            exts.add(suffix)
    ext_str = " ".join(sorted(exts)) if exts else ""

    # Summary counts
    total_scannable = sum(scannable_by_dir.values())
    total_indexed = sum(indexed_by_dir.values())

    # Format table
    lines = ["📂 索引覆盖"]

    # Determine column widths
    all_rows = indexed_rows + excluded_rows + skipped_rows
    name_w = max((len(r[1]) for r in all_rows), default=10)
    name_w = max(name_w, 6) + 1  # min width + padding
    cov_w = max((len(r[2]) for r in all_rows), default=4)
    cov_w = max(cov_w, 4) + 1

    def _fmt_row(icon: str, name: str, cov: str, reason: str) -> str:
        base = f"  {icon} {name:<{name_w}s} {cov:<{cov_w}s}"
        return f"{base} {reason}" if reason else base

    if indexed_rows:
        lines.append("  ── 已索引 ──")
        for row in indexed_rows:
            lines.append(_fmt_row(*row))

    if excluded_rows:
        lines.append("  ── 已排除 ──")
        for row in excluded_rows:
            lines.append(_fmt_row(*row))

    if skipped_rows:
        lines.append("  ── 已跳过 ──")
        for row in skipped_rows:
            lines.append(_fmt_row(*row))

    # Summary
    summary_parts = [f"{total_indexed}/{total_scannable} 已索引"]
    if excluded_rows:
        summary_parts.append(f"{len(excluded_rows)} 目录排除")
    if ext_str:
        summary_parts.append(f"支持: {ext_str}")
    lines.append(f"\n  总计: {' · '.join(summary_parts)}")

    return "\n".join(lines)


def _find_exclude_reason(
    dir_name: str,
    all_excludes: list[str],
    custom_excludes: list[str],
    test_excludes: list[str] | None = None,
) -> tuple[str, str]:
    """Find the first matching exclusion pattern for a directory name.

    Returns (pattern, source) where source is one of:
      "自定义"   — user-configured via manon_configure_excludes
      "测试框架" — auto-detected test framework patterns
      "内置"     — from _ALWAYS_EXCLUDE (universal patterns)
      "gitignore" — from .gitignore
    """
    import fnmatch
    test_paths = [f"{dir_name}/", f"{dir_name}/dummy.py", dir_name]
    _always_set = set(_ALWAYS_EXCLUDE)
    _test_set = set(test_excludes or [])

    def _matches(pat: str) -> bool:
        """Check if pattern matches the directory name, handling ** glob."""
        for tp in test_paths:
            if fnmatch.fnmatch(tp, pat) or fnmatch.fnmatch(dir_name, pat):
                return True
        # fnmatch doesn't understand **; strip **/ prefix for simple matching
        simple = pat.lstrip("*").lstrip("/")
        if simple and (dir_name == simple or dir_name == simple.rstrip("/**")):
            return True
        return False

    # Check custom excludes first (most specific)
    for pat in custom_excludes:
        if _matches(pat):
            return pat, "自定义"

    # Check test framework excludes
    for pat in (test_excludes or []):
        if _matches(pat):
            return pat, "测试框架"

    # Check _ALWAYS_EXCLUDE
    for pat in _ALWAYS_EXCLUDE:
        if _matches(pat):
            return pat, "内置"

    # Check .gitignore patterns (everything else)
    for pat in all_excludes:
        if pat in _always_set or pat in _test_set:
            continue
        if _matches(pat):
            return pat, "gitignore"

    return dir_name, ""


def set_custom_excludes(local_path: str, patterns: list[str]) -> None:
    """Save custom exclusion patterns to project registry."""
    proj = get_project(local_path)
    if proj:
        proj["custom_excludes"] = patterns
        set_project(local_path, proj)


# ── Smart directory analysis signals ─────────────────

def collect_directory_signals(local_path: str) -> dict:
    """Collect per-directory metadata signals for LLM-based index relevance analysis.

    Gathers extension distribution, file counts, sample filenames, and build
    config presence for each top-level directory that isn't already excluded by
    built-in rules (.git, node_modules, etc.).

    Returns dict with supported_languages and per-directory signal data.
    """
    from codeindex.scanner import should_exclude
    from codeindex.detector import quick_detect_languages
    try:
        from codeindex.parser import get_all_extensions
        all_exts = get_all_extensions()
    except ImportError:
        from codeindex.parser import FILE_EXTENSIONS
        all_exts = FILE_EXTENSIONS

    root = Path(local_path).resolve()
    config, _, _ = _load_scan_config(local_path)

    # Project-level supported languages (detect all, including generic)
    supported_langs = quick_detect_languages(root, all_exts)

    _BUILD_CONFIG_FILES = [
        "package.json", "tsconfig.json", "Cargo.toml",
        "pyproject.toml", "go.mod", "build.gradle", "pom.xml",
        "CMakeLists.txt", "Makefile", "setup.py", "setup.cfg",
    ]

    dirs: dict[str, dict] = {}
    try:
        items = sorted(root.iterdir())
    except OSError:
        return {"supported_languages": supported_langs, "directories": {}}

    for item in items:
        if not item.is_dir():
            continue
        # Skip directories already excluded by built-in / gitignore rules
        if should_exclude(item, config.exclude, root):
            continue

        ext_counts: dict[str, int] = {}
        sample_files: list[str] = []
        total = 0
        for f in item.rglob("*"):
            if not f.is_file():
                continue
            total += 1
            ext = f.suffix.lower()
            if ext:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
            if len(sample_files) < 8:
                sample_files.append(str(f.relative_to(root)))
            # Cap traversal for very large directories
            if total >= 2000:
                break

        has_config = any((item / f).exists() for f in _BUILD_CONFIG_FILES)

        # Top extensions sorted by count descending, keep top 10
        top_exts = dict(sorted(ext_counts.items(), key=lambda x: -x[1])[:10])

        dirs[item.name] = {
            "total_files": total,
            "extensions": top_exts,
            "sample_files": sample_files,
            "has_build_config": has_config,
        }

    return {
        "supported_languages": supported_langs,
        "directories": dirs,
    }


# ── Auto-detect languages + install parsers ──────────

def ensure_parsers(local_path: str) -> dict[str, str]:
    """Auto-detect project languages and install missing tree-sitter parsers.

    Now delegates to codeindex's built-in functionality.

    Returns dict mapping language → status ("already_installed" | "installed" | "failed").
    """
    from codeindex.detector import quick_detect_languages
    from codeindex.parser import FILE_EXTENSIONS
    from codeindex.parser_installer import install_parsers

    root = Path(local_path).resolve()
    langs = quick_detect_languages(root, FILE_EXTENSIONS)

    if not langs:
        log.info("No supported languages detected in %s", local_path)
        return {}

    return install_parsers(langs)


# ── Decorator enrichment (fallback if parser doesn't extract) ─────

def _enrich_annotations(pr_dict: dict, source: str, file_path: str) -> dict:
    """Add decorator/annotation data to symbols if the parser didn't extract them.

    Uses regex-based extraction as a fallback when the tree-sitter parser
    doesn't support annotation extraction (e.g., older codeindex versions
    or cached module state).
    """
    symbols = pr_dict.get("symbols", [])
    if not symbols:
        return pr_dict

    # Check if any symbol already has annotations — if so, parser handled it
    if any(s.get("annotations") for s in symbols):
        return pr_dict

    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if ext not in ("py", "ts", "tsx", "js", "jsx", "php", "phtml", "java"):
        return pr_dict

    lines = source.split("\n") if source else []
    if not lines:
        return pr_dict

    # Build line→symbol mapping
    sym_by_line: dict[int, dict] = {}
    for s in symbols:
        ls = s.get("line_start", 0)
        if ls > 0:
            sym_by_line[ls] = s

    if ext == "py":
        _enrich_python_decorators(lines, sym_by_line)
    elif ext in ("ts", "tsx", "js", "jsx"):
        _enrich_ts_decorators(lines, sym_by_line)
    elif ext in ("php", "phtml"):
        _enrich_php_attributes(lines, sym_by_line)
    elif ext == "java":
        _enrich_java_annotations(lines, sym_by_line)

    return pr_dict


import re

_PY_DECORATOR_RE = re.compile(r"^\s*@([\w.]+)")
_TS_DECORATOR_RE = re.compile(r"^\s*@(\w+)")
_PHP_ATTR_RE = re.compile(r"#\[(\w+)")
_JAVA_ANN_RE = re.compile(r"^\s*@(\w+)")


def _enrich_python_decorators(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        decorators = []
        # Scan lines above the symbol definition for decorators
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _PY_DECORATOR_RE.match(line)
            if m:
                decorators.append(m.group(1))
            elif line.strip() and not line.strip().startswith("#"):
                break
        if decorators:
            sym.setdefault("annotations", [])
            sym["annotations"] = [{"name": d, "arguments": {}} for d in reversed(decorators)]


def _enrich_ts_decorators(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        decorators = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _TS_DECORATOR_RE.match(line)
            if m:
                decorators.append(m.group(1))
            elif line.strip() and not line.strip().startswith("//"):
                break
        if decorators:
            sym["annotations"] = [{"name": d, "arguments": {}} for d in reversed(decorators)]


def _enrich_php_attributes(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        attrs = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i].strip()
            m = _PHP_ATTR_RE.search(line)
            if m:
                attrs.append(m.group(1))
            elif line and not line.startswith("//") and not line.startswith("*"):
                break
        if attrs:
            sym["annotations"] = [{"name": a, "arguments": {}} for a in reversed(attrs)]


def _enrich_java_annotations(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        anns = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _JAVA_ANN_RE.match(line)
            if m:
                anns.append(m.group(1))
            elif line.strip() and not line.strip().startswith("//") and not line.strip().startswith("*"):
                break
        if anns:
            sym["annotations"] = [{"name": a, "arguments": {}} for a in reversed(anns)]


# ── File scanning + AST extraction ───────────────────

def _resolve_relative_callees(parse_dict: dict, rel_path: str) -> dict:
    """Resolve relative-path callees (./mod.func) to full module IDs.

    The TypeScript parser resolves import aliases to relative paths like
    ``./chat-helpers.streamLLMWithTools``.  The server pipeline expects
    full dot-separated module IDs like
    ``electron.orchestrator.chat-helpers.streamLLMWithTools``.
    """
    import posixpath

    calls = parse_dict.get("calls")
    if not calls:
        return parse_dict

    # e.g. "electron/orchestrator/skill-router.ts" → "electron/orchestrator"
    file_dir = posixpath.dirname(rel_path)

    changed = False
    for call in calls:
        callee = call.get("callee", "")
        if not (callee.startswith("./") or callee.startswith("../")):
            continue
        # Split first dot-segment (relative module) from the rest (symbol chain)
        # e.g. "./chat-helpers.streamLLMWithTools" → "./chat-helpers", "streamLLMWithTools"
        dot_idx = callee.find(".", 2 if callee.startswith("./") else 3)
        if dot_idx == -1:
            # No symbol part, just a module reference
            mod_rel = callee
            symbol = ""
        else:
            mod_rel = callee[:dot_idx]
            symbol = callee[dot_idx + 1:]

        # Resolve relative path: "./chat-helpers" relative to "electron/orchestrator"
        resolved = posixpath.normpath(posixpath.join(file_dir, mod_rel))
        # Convert slashes to dots: "electron/orchestrator/chat-helpers" → "electron.orchestrator.chat-helpers"
        module_id = resolved.replace("/", ".")

        call["callee"] = f"{module_id}.{symbol}" if symbol else module_id
        changed = True

    return parse_dict


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def scan_and_parse(
    local_path: str,
    old_hashes: dict[str, str],
    *,
    max_files: int = 0,
) -> tuple[list[dict], list[str], dict[str, str]]:
    """Scan directory, parse changed files, return sync payload.

    Auto-detects project languages and installs missing parsers before scanning.

    Returns (file_results, deleted_files, new_hashes).
    """
    # Auto-install missing tree-sitter parsers
    ensure_parsers(local_path)

    from codeindex.scanner import scan_directory
    from codeindex.parser import parse_file

    config, root, _test_exc = _load_scan_config(local_path)
    scan_result = scan_directory(root, config, root)

    new_hashes: dict[str, str] = {}
    file_results: list[dict] = []

    for f in scan_result.files:
        rel = str(f.relative_to(root)).replace("\\", "/")
        h = _file_hash(f)
        new_hashes[rel] = h
        if old_hashes.get(rel) == h:
            continue
        if max_files > 0 and len(file_results) >= max_files:
            continue
        pr = parse_file(f)
        if pr.error:
            log.warning("Parse error %s: %s", rel, pr.error)
            continue
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning("Failed to read %s: %s", rel, e)
            source = ""
        pr_dict = pr.to_dict()
        pr_dict = _resolve_relative_callees(pr_dict, rel)
        pr_dict = _enrich_annotations(pr_dict, source, rel)
        file_results.append({
            "rel_path": rel, "hash": h,
            "source": source, "parse_result": pr_dict,
        })

    deleted_files = list(set(old_hashes.keys()) - set(new_hashes.keys()))
    return file_results, deleted_files, new_hashes


def count_scannable_files(local_path: str) -> int:
    """Quick count of scannable files without parsing."""
    from codeindex.scanner import scan_directory
    config, root, _test_exc = _load_scan_config(local_path)
    scan_result = scan_directory(root, config, root)
    return len(scan_result.files)


async def sync_to_server(repo_id: str, file_results: list, deleted_files: list, *, full_reindex: bool = False) -> dict:
    """Upload AST data to saas/ in batches."""
    from shared import saas_client

    last_result: dict = {}
    for i in range(0, max(len(file_results), 1), SYNC_BATCH_SIZE):
        batch = file_results[i:i + SYNC_BATCH_SIZE]
        last_result = await saas_client.sync_ast(
            repo_id, batch,
            deleted_files if i == 0 else [],
            full_reindex=full_reindex and i == 0,
        )
    return last_result
