"""File scanning and AST extraction."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger("manon.ast_sync")

SYNC_BATCH_SIZE = 50


def _file_hash(path: Path) -> str:
    """Compute SHA256 hash of file contents."""
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

    Args:
        local_path: Project root path
        old_hashes: Previous file hashes (rel_path -> hash)
        max_files: Maximum files to parse (0 = unlimited)

    Returns (file_results, deleted_files, new_hashes).
    """
    from codeindex.scanner import scan_directory
    from codeindex.parser import parse_file
    from .config import _load_scan_config
    from .parser_utils import ensure_parsers, _resolve_relative_callees, _enrich_annotations
    from matrixone_graph.pipeline import chunk_file_from_dict

    # Auto-install missing tree-sitter parsers
    ensure_parsers(local_path)

    config, root, _test_exc = _load_scan_config(local_path)
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
        chunks = chunk_file_from_dict(source, pr_dict, rel)
        file_results.append({
            "rel_path": rel, "hash": h,
            "parse_result": pr_dict,
            "chunks": chunks,
        })

    deleted_files = list(set(old_hashes.keys()) - set(new_hashes.keys()))
    return file_results, deleted_files, new_hashes


def count_scannable_files(local_path: str) -> int:
    """Quick count of scannable files without parsing."""
    from codeindex.scanner import scan_directory
    from .config import _load_scan_config

    config, root, _test_exc = _load_scan_config(local_path)
    scan_result = scan_directory(root, config, root)
    return len(scan_result.files)


async def sync_to_server(repo_id: str, file_results: list, deleted_files: list, *, full_reindex: bool = False) -> dict:
    """Upload AST data to saas/ in batches."""
    from shared import saas_client

    last_result: dict = {}
    for i in range(0, len(file_results), SYNC_BATCH_SIZE):
        batch = file_results[i:i + SYNC_BATCH_SIZE]
        payload = {
            "files": batch,
            "deleted_files": deleted_files if i == 0 else [],
            "full_reindex": full_reindex if i == 0 else False,
        }
        last_result = await saas_client.post(f"/api/v1/repos/{repo_id}/sync-ast", payload)
    return last_result
