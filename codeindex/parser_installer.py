"""Automatic tree-sitter parser installation."""
import logging
import subprocess
import sys

log = logging.getLogger("codeindex.parser_installer")

# Language -> pip package mapping
LANG_TO_PACKAGE = {
    "python": "tree-sitter-python",
    "javascript": "tree-sitter-javascript",
    "typescript": "tree-sitter-typescript",
    "tsx": "tree-sitter-typescript",
    "php": "tree-sitter-php",
    "java": "tree-sitter-java",
}

# Pip mirrors for faster installation
PIP_MIRRORS = [
    None,  # default PyPI
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
]


def check_parser_installed(language: str) -> bool:
    """Check if tree-sitter parser is installed."""
    try:
        if language in ("typescript", "tsx"):
            __import__("tree_sitter_typescript")
        elif language == "javascript":
            __import__("tree_sitter_javascript")
        elif language == "python":
            __import__("tree_sitter_python")
        elif language == "php":
            __import__("tree_sitter_php")
        elif language == "java":
            __import__("tree_sitter_java")
        else:
            return False
        return True
    except ImportError:
        return False


def install_parsers(languages: set[str], timeout: int = 30) -> dict[str, str]:
    """Install missing parsers for detected languages.

    Args:
        languages: Set of language names to install parsers for
        timeout: Maximum seconds to wait for pip install (default 30)

    Returns:
        Dict mapping language -> status ("installed" | "already_installed" | "failed")
    """
    results = {}
    to_install = []

    # Check which parsers are missing
    for lang in languages:
        if check_parser_installed(lang):
            results[lang] = "already_installed"
        else:
            pkg = LANG_TO_PACKAGE.get(lang)
            if pkg and pkg not in to_install:
                to_install.append(pkg)
                results[lang] = "pending"

    if not to_install:
        return results

    log.info("Installing parsers: %s", ", ".join(to_install))

    # Try default PyPI first (fastest for most users)
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + to_install

    try:
        subprocess.check_call(cmd, timeout=timeout)
        for lang in languages:
            if results.get(lang) == "pending":
                results[lang] = "installed"
        log.info("Parsers installed successfully")
        return results
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.warning("Install via PyPI failed: %s, trying mirrors", e)

    # Try mirrors only if default failed
    for mirror in PIP_MIRRORS[1:]:  # Skip None (already tried)
        cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + to_install
        cmd += ["-i", mirror]

        try:
            subprocess.check_call(cmd, timeout=timeout)
            for lang in languages:
                if results.get(lang) == "pending":
                    results[lang] = "installed"
            log.info("Parsers installed successfully via %s", mirror)
            return results
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.warning("Install via %s failed: %s", mirror, e)
            continue

    # All attempts failed
    for lang in languages:
        if results.get(lang) == "pending":
            results[lang] = "failed"

    return results
