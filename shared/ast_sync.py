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

# Universal directories to always exclude — covers all major languages/frameworks.
# .gitignore is also parsed automatically for project-specific patterns.
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


# ── Config loading with .gitignore support ────────────

def _load_scan_config(local_path: str):
    """Load codeindex Config with auto language detection and augment with .gitignore + custom excludes."""
    from codeindex.config import Config

    root = Path(local_path).resolve()

    # Use new API: auto-detects languages, installs parsers, generates smart config
    config = Config.load_with_auto_setup(root)

    # Merge: existing excludes + always-exclude + .gitignore + custom
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

    # Load custom excludes from project registry
    proj = get_project(local_path)
    if proj:
        for pat in proj.get("custom_excludes", []):
            excludes.add(pat)

    config.exclude = list(excludes)
    return config, root


def preview_project_structure(local_path: str) -> str:
    """List top-level directory structure with file counts for LLM review.

    Returns a formatted string showing each top-level directory, its file count,
    and whether it's currently excluded. The calling LLM uses this to decide
    if additional exclusions are needed.
    """
    root = Path(local_path).resolve()
    config, _ = _load_scan_config(local_path)
    from codeindex.scanner import should_exclude

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
        # Quick file count (non-recursive, cap at 100 to stay fast)
        try:
            count = sum(1 for _ in zip(range(500), item.rglob("*")) if True)
            count_str = f"{count}+" if count >= 500 else str(count)
        except OSError:
            count_str = "?"
        marker = "✗ 已排除" if excluded else "○ 待扫描"
        lines.append(f"  {marker}  {name}/  ({count_str} 文件)")

    return "\n".join(lines) if lines else "（空目录）"


def set_custom_excludes(local_path: str, patterns: list[str]) -> None:
    """Save custom exclusion patterns to project registry."""
    proj = get_project(local_path)
    if proj:
        proj["custom_excludes"] = patterns
        set_project(local_path, proj)


# ── Auto-detect languages + install parsers ──────────

def ensure_parsers(local_path: str) -> dict[str, str]:
    """Auto-detect project languages and install missing tree-sitter parsers.

    Now delegates to codeindex's built-in functionality.

    Returns dict mapping language → status ("already_installed" | "installed" | "failed").
    """
    from codeindex.detector import quick_detect_languages
    from codeindex.parser import FILE_EXTENSIONS
    from codeindex.parser_installer import install_parsers

    root = Path(local_path).resolve()
    langs = quick_detect_languages(root, FILE_EXTENSIONS)

    if not langs:
        log.info("No supported languages detected in %s", local_path)
        return {}

    return install_parsers(langs)


# ── Decorator enrichment (fallback if parser doesn't extract) ─────

def _enrich_annotations(pr_dict: dict, source: str, file_path: str) -> dict:
    """Add decorator/annotation data to symbols if the parser didn't extract them.

    Uses regex-based extraction as a fallback when the tree-sitter parser
    doesn't support annotation extraction (e.g., older codeindex versions
    or cached module state).
    """
    symbols = pr_dict.get("symbols", [])
    if not symbols:
        return pr_dict

    # Check if any symbol already has annotations — if so, parser handled it
    if any(s.get("annotations") for s in symbols):
        return pr_dict

    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if ext not in ("py", "ts", "tsx", "js", "jsx", "php", "phtml", "java"):
        return pr_dict

    lines = source.split("\n") if source else []
    if not lines:
        return pr_dict

    # Build line→symbol mapping
    sym_by_line: dict[int, dict] = {}
    for s in symbols:
        ls = s.get("line_start", 0)
        if ls > 0:
            sym_by_line[ls] = s

    if ext == "py":
        _enrich_python_decorators(lines, sym_by_line)
    elif ext in ("ts", "tsx", "js", "jsx"):
        _enrich_ts_decorators(lines, sym_by_line)
    elif ext in ("php", "phtml"):
        _enrich_php_attributes(lines, sym_by_line)
    elif ext == "java":
        _enrich_java_annotations(lines, sym_by_line)

    return pr_dict


import re

_PY_DECORATOR_RE = re.compile(r"^\s*@([\w.]+)")
_TS_DECORATOR_RE = re.compile(r"^\s*@(\w+)")
_PHP_ATTR_RE = re.compile(r"#\[(\w+)")
_JAVA_ANN_RE = re.compile(r"^\s*@(\w+)")


def _enrich_python_decorators(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        decorators = []
        # Scan lines above the symbol definition for decorators
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _PY_DECORATOR_RE.match(line)
            if m:
                decorators.append(m.group(1))
            elif line.strip() and not line.strip().startswith("#"):
                break
        if decorators:
            sym.setdefault("annotations", [])
            sym["annotations"] = [{"name": d, "arguments": {}} for d in reversed(decorators)]


def _enrich_ts_decorators(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        decorators = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _TS_DECORATOR_RE.match(line)
            if m:
                decorators.append(m.group(1))
            elif line.strip() and not line.strip().startswith("//"):
                break
        if decorators:
            sym["annotations"] = [{"name": d, "arguments": {}} for d in reversed(decorators)]


def _enrich_php_attributes(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        attrs = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i].strip()
            m = _PHP_ATTR_RE.search(line)
            if m:
                attrs.append(m.group(1))
            elif line and not line.startswith("//") and not line.startswith("*"):
                break
        if attrs:
            sym["annotations"] = [{"name": a, "arguments": {}} for a in reversed(attrs)]


def _enrich_java_annotations(lines: list[str], sym_by_line: dict[int, dict]):
    for line_start, sym in sym_by_line.items():
        anns = []
        for i in range(line_start - 2, max(line_start - 10, -1), -1):
            if i < 0:
                break
            line = lines[i]
            m = _JAVA_ANN_RE.match(line)
            if m:
                anns.append(m.group(1))
            elif line.strip() and not line.strip().startswith("//") and not line.strip().startswith("*"):
                break
        if anns:
            sym["annotations"] = [{"name": a, "arguments": {}} for a in reversed(anns)]


# ── File scanning + AST extraction ───────────────────

def _resolve_relative_callees(parse_dict: dict, rel_path: str) -> dict:
    """Resolve relative-path callees (./mod.func) to full module IDs.

    The TypeScript parser resolves import aliases to relative paths like
    ``./chat-helpers.streamLLMWithTools``.  The server pipeline expects
    full dot-separated module IDs like
    ``electron.orchestrator.chat-helpers.streamLLMWithTools``.
    """
    import posixpath

    calls = parse_dict.get("calls")
    if not calls:
        return parse_dict

    # e.g. "electron/orchestrator/skill-router.ts" → "electron/orchestrator"
    file_dir = posixpath.dirname(rel_path)

    changed = False
    for call in calls:
        callee = call.get("callee", "")
        if not (callee.startswith("./") or callee.startswith("../")):
            continue
        # Split first dot-segment (relative module) from the rest (symbol chain)
        # e.g. "./chat-helpers.streamLLMWithTools" → "./chat-helpers", "streamLLMWithTools"
        dot_idx = callee.find(".", 2 if callee.startswith("./") else 3)
        if dot_idx == -1:
            # No symbol part, just a module reference
            mod_rel = callee
            symbol = ""
        else:
            mod_rel = callee[:dot_idx]
            symbol = callee[dot_idx + 1:]

        # Resolve relative path: "./chat-helpers" relative to "electron/orchestrator"
        resolved = posixpath.normpath(posixpath.join(file_dir, mod_rel))
        # Convert slashes to dots: "electron/orchestrator/chat-helpers" → "electron.orchestrator.chat-helpers"
        module_id = resolved.replace("/", ".")

        call["callee"] = f"{module_id}.{symbol}" if symbol else module_id
        changed = True

    return parse_dict


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

    config, root = _load_scan_config(local_path)
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
        pr_dict = pr.to_dict()
        pr_dict = _resolve_relative_callees(pr_dict, rel)
        pr_dict = _enrich_annotations(pr_dict, source, rel)
        file_results.append({
            "rel_path": rel, "hash": h,
            "source": source, "parse_result": pr_dict,
        })

    deleted_files = list(set(old_hashes.keys()) - set(new_hashes.keys()))
    return file_results, deleted_files, new_hashes


def count_scannable_files(local_path: str) -> int:
    """Quick count of scannable files without parsing."""
    from codeindex.scanner import scan_directory
    config, root = _load_scan_config(local_path)
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
