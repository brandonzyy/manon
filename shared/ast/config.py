"""Configuration loading with .gitignore support."""
from __future__ import annotations

from pathlib import Path

# Universal directories to always exclude
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


def _load_scan_config(local_path: str):
    """Load codeindex Config with auto language detection and augment with .gitignore + custom excludes + test patterns.

    Returns (config, root, test_excludes) where test_excludes is the list of
    auto-detected test framework exclusion patterns.
    """
    from codeindex.config import Config
    from .project import get_project
    from .analysis import detect_test_patterns

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


def set_custom_excludes(local_path: str, patterns: list[str]) -> None:
    """Save custom exclusion patterns to project registry."""
    from .project import get_project, set_project

    proj = get_project(local_path)
    if proj:
        proj["custom_excludes"] = patterns
        set_project(local_path, proj)


def get_always_exclude() -> list[str]:
    """Get the list of always-excluded patterns."""
    return _ALWAYS_EXCLUDE.copy()
