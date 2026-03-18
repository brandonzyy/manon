"""Directory scanner for codeindex."""

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass
class ScanResult:
    """Result of scanning a directory."""

    path: Path
    files: list[Path]
    subdirs: list[Path]


LANGUAGE_EXTENSIONS = {
    "python": [".py"],
    "php": [".php", ".phtml"],
    "java": [".java"],
    "typescript": [".ts", ".tsx"],
    "tsx": [".tsx"],
    "javascript": [".js", ".jsx"],
}


def get_language_extensions(languages: list[str]) -> set[str]:
    """Get file extensions for specified languages."""
    extensions = set()
    for lang in languages:
        extensions.update(LANGUAGE_EXTENSIONS.get(lang, []))
    return extensions


def _compute_rel_path(path: Path, base_path: Path) -> str:
    """Compute normalized relative path string, falling back gracefully on Windows."""
    try:
        return str(path.relative_to(base_path)).replace("\\", "/")
    except ValueError:
        try:
            return str(path.resolve().relative_to(base_path.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")


def _matches_exclude_pattern(rel_path: str, path_str: str, pattern: str) -> bool:
    """Check if rel_path or path_str matches a single exclude pattern."""
    if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(path_str, pattern):
        return True
    if "**" not in pattern:
        return False
    if fnmatch.fnmatch(rel_path, pattern.replace("**", "*")):
        return True
    core = pattern.strip("*/")
    if core and core in rel_path.split("/"):
        return True
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        if fnmatch.fnmatch(rel_path, suffix):
            return True
        if suffix.endswith("/**") and fnmatch.fnmatch(rel_path, suffix[:-3]):
            return True
    return False


def should_exclude(path: Path, exclude_patterns: list[str], base_path: Path) -> bool:
    """Check if path matches any exclude pattern."""
    rel_path = _compute_rel_path(path, base_path)
    path_str = str(path)
    return any(_matches_exclude_pattern(rel_path, path_str, p) for p in exclude_patterns)


def scan_directory(
    path: Path,
    config: Config,
    base_path: Path | None = None,
    recursive: bool = True
) -> ScanResult:
    """
    Scan a directory and return its contents.

    Args:
        path: Directory to scan
        config: Configuration object
        base_path: Base path for relative pattern matching
        recursive: Whether to scan subdirectories recursively

    Returns:
        ScanResult with files and subdirectories
    """
    if base_path is None:
        base_path = path

    files: list[Path] = []
    subdirs: list[Path] = []

    if not path.exists() or not path.is_dir():
        return ScanResult(path=path, files=[], subdirs=[])

    for item in sorted(path.iterdir()):
        # Skip excluded paths
        if should_exclude(item, config.exclude, base_path):
            continue

        if item.is_file():
            # Filter by language/extension using unified extension map
            if item.suffix in get_language_extensions(config.languages):
                files.append(item)
        elif item.is_dir() and recursive:
            # Recursively scan subdirectories
            sub_result = scan_directory(item, config, base_path, recursive)
            files.extend(sub_result.files)
            subdirs.extend(sub_result.subdirs)
            subdirs.append(item)  # Track the subdirectory itself

    return ScanResult(path=path, files=files, subdirs=subdirs)



