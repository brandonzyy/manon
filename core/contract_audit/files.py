"""Wide-net file enumeration for contract audit.

The knowledge graph indexes only source files in parser-supported languages, and
drops tool scripts (``core/script_classifier``). Contract audit needs a wider net:
a config key is only dead if *nothing* reads it — including shell scripts, deploy
manifests, compose files and docs. So this module does its own enumeration rather
than reusing ``codeindex.scanner``.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Directories that never hold first-party facts. `.worktrees` is here because an
# isolated worktree is a copy of the main tree: counting it doubles every fact.
_HARD_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", ".pnpm-store", ".yarn", "bower_components",
    "__pycache__", ".venv", "venv", ".tox", ".nox",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".cache",
    "dist", "build", "out", "target", "_build",
    ".next", ".nuxt", ".output", ".svelte-kit", ".turbo",
    "coverage", "htmlcov", ".nyc_output",
    ".idea", ".vscode", ".worktrees", ".playwright-mcp",
    "vendor", "third_party",
})

# kind drives how a hit is weighted: a route referenced only from a doc is not
# the same as one called from the frontend.
_KIND_BY_EXT = {
    ".py": "code", ".rb": "code", ".go": "code", ".java": "code", ".php": "code",
    ".ts": "code", ".tsx": "code", ".js": "code", ".jsx": "code",
    ".mjs": "code", ".cjs": "code", ".mts": "code", ".cts": "code",
    ".vue": "web", ".svelte": "web", ".html": "web", ".htm": "web",
    ".sql": "sql",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".fish": "shell",
    ".yaml": "config", ".yml": "config", ".toml": "config", ".ini": "config",
    ".cfg": "config", ".conf": "config", ".json": "config", ".env": "config",
    ".properties": "config", ".tf": "config", ".service": "config", ".timer": "config",
    ".md": "doc", ".mdx": "doc", ".rst": "doc", ".txt": "doc",
}

# Matched against path *tokens*, never as substrings. `new-api-latest` splits to
# {new, api, latest} and holds no test token, while a substring search finds
# "test" inside "latest" and classifies every file under that directory as a
# test — which silently drops a whole repo to the weakest evidence tier, so every
# table reports "only mentioned in tests" for code that is plainly production.
_TEST_TOKENS = frozenset({
    "test", "tests", "testdata", "testutil", "testutils", "testing",
    "conftest", "spec", "specs", "e2e", "fixture", "fixtures", "cypress",
})
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

MAX_FILE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class SourceFile:
    """One enumerated file plus its already-read text."""

    path: Path
    rel: str
    kind: str
    text: str

    @property
    def is_test(self) -> bool:
        tokens = _TOKEN_SPLIT.split(self.rel.lower())
        return any(token in _TEST_TOKENS for token in tokens)

    def lines(self) -> list[str]:
        return self.text.splitlines()


def _kind_for(rel: str) -> str | None:
    name = rel.rsplit("/", 1)[-1]
    if name.startswith(".env") or name.endswith(".env") or ".env." in name:
        return "config"
    if name in ("Dockerfile", "Makefile", "Procfile") or name.startswith("Dockerfile."):
        return "config"
    suffix = Path(name).suffix.lower()
    return _KIND_BY_EXT.get(suffix)


def _is_generated_dir(name: str) -> bool:
    """Suffixed virtualenvs and generated stores, per the indexer's own rules.

    ``.venv-p0`` is not ``.venv``; a literal name list misses it and then walks
    thirteen thousand vendored files. The indexer already knows this shape, so
    ask it rather than keeping a second list that drifts.
    """
    try:
        from core.ast.config import _should_auto_exclude_dir

        return _should_auto_exclude_dir(name)
    except Exception:
        return False


def _excluded(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch("/" + rel, pattern):
            return True
    return False


def _git_scope(root: Path) -> list[str] | None:
    """git 眼中的审计面：跟踪文件 + 未跟踪未忽略的在途工作，排除 gitignored。

    基线必须跨机可比：CI 克隆只看得到跟踪面，本机多出来的私有树（gitignore
    的前端、本地缓存）会改写 verdict——同一份代码两台机器读出两套死面
    （判例 2026-08-27：web/ 在场时 5 条 dead，缺席时降级 suspect / 消失，
    CI 首跑即红）。--exclude-standard 走 gitignore，所以在途的未跟踪文件
    仍然算证据，机器私有的不算。git 不在 / 不是工作树时返回 None，回退全量走查。
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z",
             "--cached", "--others", "--exclude-standard"],
            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]


def _readable(entry: Path, root: Path, patterns: list[str]) -> SourceFile | None:
    """共享的逐文件收口：kind / 豁免 / 大小 / 可读性，两条枚举路径同款。"""
    rel = entry.relative_to(root).as_posix()
    kind = _kind_for(rel)
    if kind is None or _excluded(rel, patterns):
        return None
    try:
        if entry.is_symlink() or entry.stat().st_size > MAX_FILE_BYTES:
            return None
        text = entry.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return SourceFile(path=entry, rel=rel, kind=kind, text=text)


def enumerate_files(root: Path, extra_excludes: list[str] | None = None) -> list[SourceFile]:
    """Enumerate the audit surface: git-visible files, or a full walk outside a work tree."""
    patterns = list(extra_excludes or [])
    files: list[SourceFile] = []
    scope = _git_scope(root)
    if scope is not None:
        for rel in scope:
            sf = _readable(root / rel, root, patterns)
            if sf is not None:
                files.append(sf)
        files.sort(key=lambda f: f.rel)
        return files
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in _HARD_SKIP_DIRS or _is_generated_dir(entry.name):
                    continue
                rel_dir = entry.relative_to(root).as_posix()
                if _excluded(rel_dir + "/", patterns) or _excluded("**/" + entry.name + "/**", patterns):
                    continue
                stack.append(entry)
                continue
            sf = _readable(entry, root, patterns)
            if sf is not None:
                files.append(sf)
    files.sort(key=lambda f: f.rel)
    return files


def project_excludes(local_path: str) -> list[str]:
    """Custom excludes the user configured for indexing, reused as audit excludes."""
    try:
        from core.ast.project import get_project

        project = get_project(local_path)
    except Exception:
        return []
    if not project:
        return []
    return list(project.get("custom_excludes") or [])
