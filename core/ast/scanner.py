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


def _build_file_entry(f: Path, root: Path, rel: str, h: str) -> dict | None:
    """Parse a single changed file and return its sync entry, or None on error."""
    from codeindex.parser import parse_file
    from .parser_utils import _resolve_relative_callees, _enrich_annotations
    from .chunking import chunk_file_from_dict
    pr = parse_file(f)
    if pr.error:
        log.warning("Parse error %s: %s", rel, pr.error)
        return None
    try:
        source = f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        log.warning("Failed to read %s: %s", rel, e)
        source = ""
    pr_dict = pr.to_dict()
    pr_dict = _resolve_relative_callees(pr_dict, rel)
    pr_dict = _enrich_annotations(pr_dict, source, rel)
    return {"rel_path": rel, "hash": h, "parse_result": pr_dict,
            "chunks": chunk_file_from_dict(source, pr_dict, rel)}


def scan_and_parse(
    local_path: str,
    old_hashes: dict[str, str],
    *,
    max_files: int = 0,
) -> tuple[list[dict], list[str], dict[str, str]]:
    """Scan directory, parse changed files, return sync payload."""
    from codeindex.scanner import scan_directory
    from .config import _load_scan_config
    from .parser_utils import ensure_parsers

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
        entry = _build_file_entry(f, root, rel, h)
        if entry is not None:
            file_results.append(entry)

    deleted_files = list(set(old_hashes.keys()) - set(new_hashes.keys()))
    return file_results, deleted_files, new_hashes


def count_scannable_files(local_path: str) -> int:
    """Quick count of scannable files without parsing."""
    from codeindex.scanner import scan_directory
    from .config import _load_scan_config

    config, root, _test_exc = _load_scan_config(local_path)
    scan_result = scan_directory(root, config, root)
    return len(scan_result.files)


