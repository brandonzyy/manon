"""Automatic tree-sitter parser installation."""
import logging
import os
import subprocess
import sys

log = logging.getLogger("codeindex.parser_installer")

# Language -> pip package mapping. Must cover every language in parser.py's
# FILE_EXTENSIONS and _GENERIC_EXTENSIONS — a language missing here is detected
# and then silently never parsed, because no grammar is ever installed for it.
LANG_TO_PACKAGE = {
    # Specialized parsers
    "python": "tree-sitter-python",
    "javascript": "tree-sitter-javascript",
    "typescript": "tree-sitter-typescript",
    "tsx": "tree-sitter-typescript",
    "php": "tree-sitter-php",
    "java": "tree-sitter-java",
    # Generic parsers
    "go": "tree-sitter-go",
    "rust": "tree-sitter-rust",
    "c": "tree-sitter-c",
    "cpp": "tree-sitter-cpp",
    "c_sharp": "tree-sitter-c-sharp",
    "ruby": "tree-sitter-ruby",
    "swift": "tree-sitter-swift",
    "kotlin": "tree-sitter-kotlin",
    "scala": "tree-sitter-scala",
    "lua": "tree-sitter-lua",
    "r": "tree-sitter-r",  # not published to PyPI yet — install falls back per-package
    "elixir": "tree-sitter-elixir",
    "dart": "tree-sitter-dart",
    "haskell": "tree-sitter-haskell",
    "ocaml": "tree-sitter-ocaml",
    "bash": "tree-sitter-bash",
    "zig": "tree-sitter-zig",
}

# Proxies that only allow CONNECT to approved hosts reject PyPI outright.
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "http_proxy", "https_proxy", "all_proxy")

# Pip mirrors for faster installation
PIP_MIRRORS = [
    None,  # default PyPI
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
]


def check_parser_installed(language: str) -> bool:
    """Check if tree-sitter parser is installed.

    Derived from LANG_TO_PACKAGE so a new language needs one entry, not two.
    """
    pkg = LANG_TO_PACKAGE.get(language)
    if not pkg:
        return False
    try:
        __import__(pkg.replace("-", "_"))
        return True
    except ImportError:
        return False


def _try_pip_install(packages: list[str], timeout: int, mirror: str | None = None) -> bool:
    """Try installing packages via pip, optionally with a mirror. Returns True on success."""
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + packages
    if mirror:
        cmd += ["-i", mirror]

    envs: list[dict | None] = [None]
    if any(os.environ.get(v) for v in _PROXY_VARS):
        envs.append({k: v for k, v in os.environ.items() if k not in _PROXY_VARS})

    for env in envs:
        try:
            subprocess.check_call(cmd, timeout=timeout, env=env)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.warning("Install via %s failed%s: %s",
                        mirror or "PyPI", " (direct)" if env is not None else "", e)
    return False


def install_parsers(languages: set[str], timeout: int = 30) -> dict[str, str]:
    """Install missing parsers for detected languages."""
    results = {}
    to_install = []
    for lang in languages:
        if check_parser_installed(lang):
            results[lang] = "already_installed"
            continue
        pkg = LANG_TO_PACKAGE.get(lang)
        if not pkg:
            continue  # unknown language — nothing we can install
        # Mark pending per language, not per package: typescript and tsx share
        # one wheel, and gating on the queue left the second one absent from
        # the results entirely.
        results[lang] = "pending"
        if pkg not in to_install:
            to_install.append(pkg)

    if not to_install:
        return results

    log.info("Installing parsers: %s", ", ".join(to_install))
    installed: set[str] = set()
    for mirror in PIP_MIRRORS:
        if _try_pip_install(to_install, timeout, mirror):
            installed.update(to_install)
            log.info("Parsers installed successfully via %s", mirror or "PyPI")
            break
    else:
        # A batch install is all-or-nothing: one package missing from the index
        # takes down every package sharing the command. Retry singly so the
        # available grammars still land.
        if len(to_install) > 1:
            for pkg in to_install:
                if any(_try_pip_install([pkg], timeout, m) for m in PIP_MIRRORS):
                    installed.add(pkg)
                else:
                    log.warning("Parser package unavailable: %s", pkg)

    for lang in languages:
        if results.get(lang) == "pending":
            results[lang] = "installed" if LANG_TO_PACKAGE.get(lang) in installed else "failed"
    return results
