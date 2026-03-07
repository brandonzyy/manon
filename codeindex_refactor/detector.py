"""Lightweight language detection without full AST parsing."""
from pathlib import Path


# Universal exclude patterns - always skip these
UNIVERSAL_EXCLUDES = {
    'node_modules', '.git', '__pycache__', '.venv', 'venv',
    'dist', 'build', '.next', '.nuxt', 'target', '.gradle',
}


def quick_detect_languages(root: Path, file_extensions: dict[str, str], max_files: int = 500) -> set[str]:
    """Fast language detection by scanning file extensions only.

    Args:
        root: Project root directory
        file_extensions: Mapping of extension -> language (e.g., {'.py': 'python'})
        max_files: Maximum files to scan before stopping (default 500)

    Returns:
        Set of detected language names
    """
    langs = set()
    scanned = 0

    def _scan_dir(directory: Path, depth: int = 0):
        nonlocal scanned
        # Stop if we've scanned enough files or gone too deep
        if scanned >= max_files or depth > 5:
            return

        try:
            items = list(directory.iterdir())
        except (PermissionError, OSError):
            return

        # Process files first (more likely to find languages quickly)
        for item in items:
            if scanned >= max_files:
                return

            if item.is_file():
                scanned += 1
                ext = item.suffix.lower()
                if lang := file_extensions.get(ext):
                    langs.add(lang)
            elif item.is_dir() and item.name not in UNIVERSAL_EXCLUDES:
                _scan_dir(item, depth + 1)

    _scan_dir(root)
    return langs
