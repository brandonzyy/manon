"""Analysis functions - test detection, coverage analysis, smart signals."""
from __future__ import annotations

import fnmatch
from pathlib import Path


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
        # Quick file count (non-recursive, cap at 500 to stay fast)
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
    from .config import _load_scan_config, get_always_exclude
    from .project import get_project

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
            pat, source = _find_exclude_reason(d, config.exclude, custom_excludes, test_excludes)
            excluded_rows.append(("✗", d, "—", f"{source}: {pat}"))
        elif scannable == 0:
            skipped_rows.append(("○", d, "—", "无可扫描文件"))
        else:
            pct = int(100 * indexed / scannable) if scannable > 0 else 0
            cov = f"{indexed}/{scannable} ({pct}%)"
            if indexed == 0:
                indexed_rows.append(("⚠", d, cov, "未索引"))
            elif indexed < scannable:
                indexed_rows.append(("△", d, cov, "部分索引"))
            else:
                indexed_rows.append(("✓", d, cov, "已索引"))

    # Format output
    lines = ["  📂 索引覆盖分析"]
    for icon, name, cov, reason in indexed_rows + skipped_rows + excluded_rows:
        lines.append(f"    {icon} {name:<20s} {cov:<15s} {reason}")

    total_scannable = sum(scannable_by_dir.values())
    total_indexed = len(indexed_hashes)
    if total_scannable > 0:
        pct = int(100 * total_indexed / total_scannable)
        lines.append(f"\n  总计: {total_indexed}/{total_scannable} 文件已索引 ({pct}%)")

    return "\n".join(lines)


def _find_exclude_reason(
    dir_name: str, all_excludes: list[str],
    custom_excludes: list[str], test_excludes: list[str] | None,
) -> tuple[str, str]:
    """Find which exclusion pattern matched this directory and its source.

    Returns (pattern, source) where source is one of: "自定义", "测试框架", "内置", "gitignore".
    """
    from .config import get_always_exclude

    _ALWAYS_EXCLUDE = get_always_exclude()

    # Build test paths for matching
    test_paths = [
        f"**/{dir_name}/**",
        f"**/{dir_name}",
        f"{dir_name}/**",
        dir_name,
    ]

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


def collect_directory_signals(local_path: str) -> dict:
    """Collect per-directory metadata signals for LLM-based index relevance analysis.

    Gathers extension distribution, file counts, sample filenames, and build
    config presence for each top-level directory that isn't already excluded by
    built-in rules (.git, node_modules, etc.).

    Returns dict with supported_languages and per-directory signal data.
    """
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
        name = item.name

        # Skip if already excluded by built-in rules
        if should_exclude(item, config.exclude, root):
            continue

        # Collect signals
        exts: dict[str, int] = {}
        samples: list[str] = []
        file_count = 0
        has_build_config = False

        try:
            for f in item.rglob("*"):
                if not f.is_file():
                    continue
                file_count += 1
                if file_count > 1000:  # Cap to avoid slow scans
                    break

                # Extension distribution
                ext = f.suffix.lower()
                if ext:
                    exts[ext] = exts.get(ext, 0) + 1

                # Sample filenames (first 5)
                if len(samples) < 5:
                    samples.append(f.name)

                # Build config detection
                if f.name in _BUILD_CONFIG_FILES:
                    has_build_config = True

        except OSError:
            continue

        if file_count > 0:
            dirs[name] = {
                "file_count": file_count,
                "extensions": exts,
                "samples": samples,
                "has_build_config": has_build_config,
            }

    return {
        "supported_languages": supported_langs,
        "directories": dirs,
    }
