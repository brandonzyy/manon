"""Local AST extraction + incremental sync to saas/ backend.

Ported from manon-mcp/server.py — shares the same ~/.manon/projects.json
cache so MCP and web clients see consistent file hashes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("manon.ast_sync")

SYNC_BATCH_SIZE = 50
PROJECTS_DIR = Path.home() / ".manon"
PROJECTS_FILE = PROJECTS_DIR / "projects.json"


# ── Project registry (shared with MCP) ──────────────

def load_projects() -> dict:
    if PROJECTS_FILE.exists():
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    return {"projects": {}}


def save_projects(data: dict) -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_project(local_path: str) -> dict | None:
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    return load_projects()["projects"].get(norm)


def set_project(local_path: str, info: dict) -> None:
    norm = str(Path(local_path).resolve()).replace("\\", "/")
    data = load_projects()
    data["projects"][norm] = info
    save_projects(data)


def find_project_by_repo_id(repo_id: str) -> tuple[str, dict] | None:
    for path, info in load_projects()["projects"].items():
        if info.get("repo_id") == repo_id:
            return path, info
    return None


# ── Auto-detect languages + install parsers ──────────

# Extension → language (mirrors codeindex.parser.FILE_EXTENSIONS)
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".php": "php", ".phtml": "php",
    ".java": "java", ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "javascript",
}

# Language → pip package name
_LANG_TO_PKG: dict[str, str] = {
    "python": "tree-sitter-python",
    "php": "tree-sitter-php",
    "java": "tree-sitter-java",
    "typescript": "tree-sitter-typescript",
    "tsx": "tree-sitter-typescript",  # same package
    "javascript": "tree-sitter-javascript",
}


def detect_languages(local_path: str) -> set[str]:
    """Scan project directory and return set of detected languages."""
    from codeindex.scanner import scan_directory
    from codeindex.config import Config

    root = Path(local_path).resolve()
    config = Config.load(root / ".codeindex.yaml")
    scan_result = scan_directory(root, config, root)

    langs: set[str] = set()
    for f in scan_result.files:
        ext = f.suffix.lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            langs.add(lang)
    return langs


def _check_parser_installed(language: str) -> bool:
    """Check if tree-sitter parser for a language is importable."""
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


def ensure_parsers(local_path: str) -> dict[str, str]:
    """Auto-detect project languages and install missing tree-sitter parsers.

    Returns dict mapping language → status ("already_installed" | "installed" | "failed").
    """
    langs = detect_languages(local_path)
    if not langs:
        log.info("No supported languages detected in %s", local_path)
        return {}

    # Deduplicate packages (tsx and typescript share the same package)
    needed_pkgs: dict[str, list[str]] = {}  # pkg → [languages]
    for lang in langs:
        pkg = _LANG_TO_PKG.get(lang)
        if pkg:
            needed_pkgs.setdefault(pkg, []).append(lang)

    results: dict[str, str] = {}
    to_install: list[str] = []

    for pkg, pkg_langs in needed_pkgs.items():
        if all(_check_parser_installed(l) for l in pkg_langs):
            for l in pkg_langs:
                results[l] = "already_installed"
        else:
            to_install.append(pkg)
            for l in pkg_langs:
                results[l] = "pending"

    if not to_install:
        log.info("All parsers already installed for: %s", ", ".join(langs))
        return results

    log.info("Installing missing parsers: %s", ", ".join(to_install))
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + to_install,
            timeout=120,
        )
        for pkg in to_install:
            for l in needed_pkgs[pkg]:
                results[l] = "installed"
        log.info("Parsers installed successfully: %s", ", ".join(to_install))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.error("Failed to install parsers: %s", e)
        for pkg in to_install:
            for l in needed_pkgs[pkg]:
                results[l] = "failed"

    return results


# ── File scanning + AST extraction ───────────────────

def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def scan_and_parse(
    local_path: str,
    old_hashes: dict[str, str],
    *,
    max_files: int = 0,
) -> tuple[list[dict], list[str], dict[str, str]]:
    """Scan directory, parse changed files, return sync payload.

    Auto-detects project languages and installs missing parsers before scanning.

    Returns (file_results, deleted_files, new_hashes).
    """
    # Auto-install missing tree-sitter parsers
    ensure_parsers(local_path)

    from codeindex.scanner import scan_directory
    from codeindex.parser import parse_file
    from codeindex.config import Config

    root = Path(local_path).resolve()
    config = Config.load(root / ".codeindex.yaml")
    scan_result = scan_directory(root, config, root)

    new_hashes: dict[str, str] = {}
    file_results: list[dict] = []

    for f in scan_result.files:
        rel = str(f.relative_to(root)).replace("\\", "/")
        h = _file_hash(f)
        new_hashes[rel] = h
        if old_hashes.get(rel) == h:
            continue
        if max_files > 0 and len(file_results) >= max_files:
            continue
        pr = parse_file(f)
        if pr.error:
            log.warning("Parse error %s: %s", rel, pr.error)
            continue
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning("Failed to read %s: %s", rel, e)
            source = ""
        file_results.append({
            "rel_path": rel, "hash": h,
            "source": source, "parse_result": pr.to_dict(),
        })

    deleted_files = list(set(old_hashes.keys()) - set(new_hashes.keys()))
    return file_results, deleted_files, new_hashes


def count_scannable_files(local_path: str) -> int:
    """Quick count of scannable files without parsing."""
    from codeindex.scanner import scan_directory
    from codeindex.config import Config
    root = Path(local_path).resolve()
    config = Config.load(root / ".codeindex.yaml")
    scan_result = scan_directory(root, config, root)
    return len(scan_result.files)


async def sync_to_server(repo_id: str, file_results: list, deleted_files: list, *, full_reindex: bool = False) -> dict:
    """Upload AST data to saas/ in batches."""
    from shared import saas_client

    last_result: dict = {}
    for i in range(0, max(len(file_results), 1), SYNC_BATCH_SIZE):
        batch = file_results[i:i + SYNC_BATCH_SIZE]
        last_result = await saas_client.sync_ast(
            repo_id, batch,
            deleted_files if i == 0 else [],
            full_reindex=full_reindex and i == 0,
        )
    return last_result
