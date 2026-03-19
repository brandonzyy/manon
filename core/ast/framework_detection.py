"""Test framework detection — auto-detect test patterns from project config files."""
from __future__ import annotations

import os
from pathlib import Path

# Minimal skip set for test file traversal (Go test detection).
_SKIP = frozenset({
    ".git", ".svn", ".hg",
    ".venv", "venv", "__pycache__", ".tox", ".nox",
    "node_modules", ".yarn", "dist", "build", "target", "_build",
    ".next", ".nuxt", ".output", ".gradle", ".m2",
    "htmlcov", ".nyc_output", "coverage", ".cache", ".tmp", ".temp",
})


def _walk(root: Path):
    """Yield all files under root, pruning common heavy directories."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP]
        for fname in filenames:
            yield Path(dirpath) / fname


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
            if any(f for f in _walk(root) if f.name.endswith("_test.go")):
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
