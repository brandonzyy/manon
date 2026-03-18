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


def _try_pip_install(packages: list[str], timeout: int, mirror: str | None = None) -> bool:
    """Try installing packages via pip, optionally with a mirror. Returns True on success."""
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + packages
    if mirror:
        cmd += ["-i", mirror]
    try:
        subprocess.check_call(cmd, timeout=timeout)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.warning("Install via %s failed: %s", mirror or "PyPI", e)
        return False


def install_parsers(languages: set[str], timeout: int = 30) -> dict[str, str]:
    """Install missing parsers for detected languages."""
    results = {}
    to_install = []
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
    for mirror in PIP_MIRRORS:
        if _try_pip_install(to_install, timeout, mirror):
            for lang in languages:
                if results.get(lang) == "pending":
                    results[lang] = "installed"
            log.info("Parsers installed successfully via %s", mirror or "PyPI")
            return results

    for lang in languages:
        if results.get(lang) == "pending":
            results[lang] = "failed"
    return results
