"""Local AST extraction + incremental sync to saas/ backend.

Ported from manon-mcp/server.py — shares the same ~/.manon/projects.json
cache so MCP and web clients see consistent file hashes.
"""
from __future__ import annotations

import hashlib
import json
import logging
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

    Returns (file_results, deleted_files, new_hashes).
    """
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
