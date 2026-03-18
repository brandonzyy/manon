"""Analysis functions - test detection, coverage analysis, smart signals."""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path

# Directories to skip during recursive traversal (basename matching).
# Kept in sync with config._ALWAYS_EXCLUDE but as simple basenames for os.walk.
_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg",
    ".venv", "venv", "__pycache__", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".yarn", ".pnpm-store", "bower_components",
    "dist", "build", "out", "target", "_build",
    ".next", ".nuxt", ".output", ".svelte-kit", ".turbo",
    ".gradle", ".m2",
    ".idea", ".vscode", ".vs", ".eclipse",
    "htmlcov", ".nyc_output", "coverage",
    ".cache", ".tmp", ".temp",
})

_SMART_ANALYSIS_SIGNATURE_VERSION = 1


def _walk_safe(root: Path, *, max_files: int = 0):
    """Recursively yield Path objects, skipping heavy directories.

    Args:
        root: Directory to walk.
        max_files: Stop after this many files (0 = unlimited).
    """
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune heavy directories in-place (modifies os.walk traversal)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            yield Path(dirpath) / fname
            count += 1
            if max_files and count >= max_files:
                return


def _file_exists_in_root(root: Path, name: str) -> bool:
    """Check if a file exists in root or one-level subdirs."""
    if (root / name).exists():
        return True
    try:
        for d in root.iterdir():
            if d.is_dir() and (d / name).exists():
                return True
    except OSError:
        pass
    return False


def _file_contains_text(root: Path, name: str, needle: str) -> bool:
    """Check if a file in root contains a string (shallow check)."""
    p = root / name
    if not p.exists():
        return False
    try:
        return needle in p.read_text(encoding="utf-8", errors="replace")[:8192]
    except Exception:
        return False


def _detect_framework_patterns(root: Path) -> tuple[set[str], dict[str, set[str]]]:
    """Phase A: config-file-based framework detection.

    Returns (patterns, frameworks_dict).
    """
    _e = lambda name: _file_exists_in_root(root, name)
    _c = lambda name, needle: _file_contains_text(root, name, needle)
    _exts = ("js", "ts", "mjs", "cjs")

    patterns: set[str] = set()
    frameworks: dict[str, set[str]] = {}

    # pytest
    if _e("conftest.py") or _e("pytest.ini") or _c("pyproject.toml", "[tool.pytest") or _c("setup.cfg", "[tool:pytest"):
        patterns.update(["**/test_*.py", "**/*_test.py", "**/conftest.py", "**/tests/**"])
        frameworks["pytest"] = {"test_*.py", "*_test.py", "tests/"}

    # Jest
    if any(_e(f"jest.config.{x}") for x in (*_exts, "json")) or _c("package.json", '"jest"'):
        patterns.update(["**/__tests__/**", "**/*.test.ts", "**/*.test.tsx", "**/*.test.js",
                         "**/*.test.jsx", "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.js", "**/*.spec.jsx"])
        frameworks["jest"] = {"__tests__/", "*.test.{ts,js}"}

    # Vitest
    if any(_e(f"vitest.config.{x}") for x in _exts) or _c("package.json", '"vitest"'):
        patterns.update(["**/__tests__/**", "**/*.test.ts", "**/*.test.tsx", "**/*.test.js",
                         "**/*.test.jsx", "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.js", "**/*.spec.jsx"])
        frameworks["vitest"] = {"__tests__/", "*.test.{ts,js}"}

    # Cypress
    if any(_e(f"cypress.config.{x}") for x in _exts) or (root / "cypress").is_dir():
        patterns.update(["**/cypress/**", "**/*.cy.ts", "**/*.cy.js"])
        frameworks["cypress"] = {"cypress/", "*.cy.{ts,js}"}

    # Playwright
    if any(_e(f"playwright.config.{x}") for x in _exts):
        patterns.update(["**/*.spec.ts", "**/*.spec.js", "**/e2e/**"])
        frameworks["playwright"] = {"*.spec.{ts,js}", "e2e/"}

    # Go
    if _e("go.mod"):
        try:
            if any(f for f in _walk_safe(root) if f.name.endswith("_test.go")):
                patterns.add("**/*_test.go")
                frameworks["go test"] = {"*_test.go"}
        except OSError:
            pass

    # Java (Maven/Gradle)
    if (root / "src" / "test").is_dir():
        patterns.add("**/src/test/**")
        frameworks["java/maven"] = {"src/test/"}

    # Rust
    if _e("Cargo.toml") and (root / "tests").is_dir():
        patterns.add("**/tests/**")
        frameworks["rust"] = {"tests/"}

    return patterns, frameworks


def detect_test_patterns(root: Path) -> tuple[list[str], list[str]]:
    """Auto-detect test frameworks and return exclusion patterns.

    Phase A: config file detection (root + one-level subdirs)
    Phase B: directory name convention detection

    Returns (deduplicated_patterns, report_lines like ["pytest: test_*.py, tests/"]).
    """
    root = root.resolve()
    patterns, frameworks = _detect_framework_patterns(root)

    # Phase B: directory name conventions (supplement)
    for dirname, pat in {"tests": "**/tests/**", "test": "**/test/**", "__tests__": "**/__tests__/**",
                         "spec": "**/spec/**", "e2e": "**/e2e/**", "cypress": "**/cypress/**"}.items():
        if (root / dirname).is_dir() and pat not in patterns:
            patterns.add(pat)
            if dirname not in frameworks:
                frameworks.setdefault("目录约定", set()).add(f"{dirname}/")

    report = [f"{fw}: {', '.join(sorted(hints))}" for fw, hints in sorted(frameworks.items())]
    return sorted(patterns), report


def preview_project_structure(local_path: str) -> str:
    """List top-level directory structure with file counts for LLM review.

    Returns a formatted string showing each top-level directory, its file count,
    and whether it's currently excluded. The calling LLM uses this to decide
    if additional exclusions are needed.
    """
    from codeindex.scanner import should_exclude
    from .config import _load_scan_config

    root = Path(local_path).resolve()
    config, _, _test_exc = _load_scan_config(local_path)

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
        # Quick file count (capped at 500 to stay fast, skips heavy dirs)
        try:
            count = sum(1 for _ in _walk_safe(item, max_files=500))
            count_str = f"{count}+" if count >= 500 else str(count)
        except OSError:
            count_str = "?"
        marker = "✗ 已排除" if excluded else "○ 待扫描"
        lines.append(f"  {marker}  {name}/  ({count_str} 文件)")

    return "\n".join(lines) if lines else "（空目录）"


def smart_analysis_signature(local_path: str) -> str:
    """Return a stable fingerprint for top-level structure changes."""
    root = Path(local_path).resolve()
    try:
        dir_names = sorted(
            item.name for item in root.iterdir()
            if item.is_dir() and item.name not in _SKIP_DIRS
        )
    except OSError:
        dir_names = []
    return json.dumps(
        {"version": _SMART_ANALYSIS_SIGNATURE_VERSION, "dirs": dir_names},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def needs_smart_analysis_refresh(local_path: str, project: dict | None) -> bool:
    """Return True when smart analysis should run or re-run."""
    if not project or not project.get("smart_analysis_done"):
        return True
    return project.get("smart_analysis_signature") != smart_analysis_signature(local_path)


def _count_by_top_dir(paths: list[str]) -> dict[str, int]:
    """Count paths by their top-level directory component."""
    by_dir: dict[str, int] = {}
    for p in paths:
        top = p.split("/")[0] if "/" in p else "."
        by_dir[top] = by_dir.get(top, 0) + 1
    return by_dir


def _classify_coverage_row(
    d: str, root: Path, scannable: int, indexed: int,
    config_excludes, custom_excludes, test_excludes, auto_excludes, should_exclude_fn,
) -> tuple[str, str, str, str, str]:
    """Classify a directory as excluded/skipped/indexed. Returns (bucket, icon, name, cov, reason)."""
    from codeindex.scanner import should_exclude
    if should_exclude(root / d, config_excludes, root):
        pat, source = _find_exclude_reason(d, config_excludes, custom_excludes, test_excludes, auto_excludes)
        return "excluded", "✗", d, "—", f"{source}: {pat}"
    if scannable == 0:
        return "skipped", "○", d, "—", "无可扫描文件"
    pct = int(100 * indexed / scannable)
    cov = f"{indexed}/{scannable} ({pct}%)"
    if indexed == 0:
        return "indexed", "⚠", d, cov, "未索引"
    if indexed < scannable:
        return "indexed", "△", d, cov, "部分索引"
    return "indexed", "✓", d, cov, "已索引"


def analyze_index_coverage(local_path: str, indexed_hashes: dict[str, str]) -> str:
    """Analyze index coverage: scannable vs indexed files per top-level directory."""
    from codeindex.scanner import scan_directory, should_exclude
    from .config import _load_scan_config, get_auto_exclude_patterns
    from .project import get_project

    root = Path(local_path).resolve()
    config, _, test_excludes = _load_scan_config(local_path)

    scan_result = scan_directory(root, config, root)
    scannable_by_dir = _count_by_top_dir([str(f.relative_to(root)).replace("\\", "/") for f in scan_result.files])
    indexed_by_dir = _count_by_top_dir(list(indexed_hashes.keys()))

    try:
        all_dirs = sorted(d.name for d in root.iterdir() if d.is_dir())
    except OSError:
        return "  无法读取目录"

    proj = get_project(local_path)
    custom_excludes = proj.get("custom_excludes", []) if proj else []
    auto_excludes = get_auto_exclude_patterns(local_path)

    indexed_rows, excluded_rows, skipped_rows = [], [], []
    for d in all_dirs:
        bucket, icon, name, cov, reason = _classify_coverage_row(
            d, root, scannable_by_dir.get(d, 0), indexed_by_dir.get(d, 0),
            config.exclude, custom_excludes, test_excludes, auto_excludes, should_exclude,
        )
        (indexed_rows if bucket == "indexed" else excluded_rows if bucket == "excluded" else skipped_rows).append((icon, name, cov, reason))

    lines = ["  📂 索引覆盖分析"]
    for icon, name, cov, reason in indexed_rows + skipped_rows + excluded_rows:
        lines.append(f"    {icon} {name:<20s} {cov:<15s} {reason}")

    total_scannable = sum(scannable_by_dir.values())
    total_indexed = len(indexed_hashes)
    if total_scannable > 0:
        lines.append(f"\n  总计: {total_indexed}/{total_scannable} 文件已索引 ({int(100 * total_indexed / total_scannable)}%)")

    return "\n".join(lines)


def _matches_dir_pattern(dir_name: str, pat: str) -> bool:
    """Check if an exclude pattern matches a directory name, handling ** globs."""
    test_paths = [f"**/{dir_name}/**", f"**/{dir_name}", f"{dir_name}/**", dir_name]
    for tp in test_paths:
        if fnmatch.fnmatch(tp, pat) or fnmatch.fnmatch(dir_name, pat):
            return True
    simple = pat.lstrip("*").lstrip("/")
    if simple and (dir_name == simple or dir_name == simple.rstrip("/**")):
        return True
    return False


def _find_exclude_reason(
    dir_name: str, all_excludes: list[str],
    custom_excludes: list[str], test_excludes: list[str] | None,
    auto_excludes: list[str] | None = None,
) -> tuple[str, str]:
    """Find which exclusion pattern matched this directory and its source.

    Returns (pattern, source) where source is one of: "自定义", "测试框架", "内置", "gitignore".
    """
    from .config import get_always_exclude
    _ALWAYS_EXCLUDE = get_always_exclude()
    _AUTO_EXCLUDE = set(auto_excludes or [])
    _always_set = set(_ALWAYS_EXCLUDE)
    _test_set = set(test_excludes or [])

    _m = lambda p: _matches_dir_pattern(dir_name, p)

    for pat in custom_excludes:
        if _m(pat): return pat, "自定义"
    for pat in (test_excludes or []):
        if _m(pat): return pat, "测试框架"
    for pat in _ALWAYS_EXCLUDE:
        if _m(pat): return pat, "内置"
    for pat in _AUTO_EXCLUDE:
        if _m(pat): return pat, "内置"
    for pat in all_excludes:
        if pat in _always_set or pat in _test_set:
            continue
        if _m(pat): return pat, "gitignore"
    return dir_name, ""


_BUILD_CONFIG_FILES = frozenset([
    "package.json", "tsconfig.json", "Cargo.toml",
    "pyproject.toml", "go.mod", "build.gradle", "pom.xml",
    "CMakeLists.txt", "Makefile", "setup.py", "setup.cfg",
])


def _collect_dir_file_signals(item: Path) -> dict | None:
    """Collect file extension distribution, samples, and build config presence for one directory."""
    exts: dict[str, int] = {}
    samples: list[str] = []
    file_count = 0
    has_build_config = False
    try:
        for f in _walk_safe(item, max_files=1000):
            file_count += 1
            ext = f.suffix.lower()
            if ext:
                exts[ext] = exts.get(ext, 0) + 1
            if len(samples) < 5:
                samples.append(f.name)
            if f.name in _BUILD_CONFIG_FILES:
                has_build_config = True
    except OSError:
        return None
    if file_count == 0:
        return None
    return {"file_count": file_count, "extensions": exts, "samples": samples, "has_build_config": has_build_config}


def collect_directory_signals(local_path: str) -> dict:
    """Collect per-directory metadata signals for LLM-based index relevance analysis."""
    from codeindex.scanner import should_exclude
    from codeindex.detector import quick_detect_languages
    from .config import _load_scan_config

    try:
        from codeindex.parser import get_all_extensions
        all_exts = get_all_extensions()
    except ImportError:
        from codeindex.parser import FILE_EXTENSIONS
        all_exts = FILE_EXTENSIONS

    root = Path(local_path).resolve()
    config, _, _ = _load_scan_config(local_path)
    supported_langs = sorted(quick_detect_languages(root, all_exts))

    try:
        items = sorted(root.iterdir())
    except OSError:
        return {"supported_languages": supported_langs, "directories": {}}

    dirs: dict[str, dict] = {}
    for item in items:
        if not item.is_dir() or should_exclude(item, config.exclude, root):
            continue
        signals = _collect_dir_file_signals(item)
        if signals:
            dirs[item.name] = signals

    return {"supported_languages": supported_langs, "directories": dirs}
