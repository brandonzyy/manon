#!/usr/bin/env python3
"""Manon scan script — run AST extraction outside MCP process.

Usage:
    python <MANON_DIR>/scripts/manon-scan.py <repo_id>

Reads project_path and old_hashes from ~/.manon/projects.json,
runs ensure_parsers + scan_and_parse, writes results to
~/.manon/scan_cache/<repo_id>.json, prints JSON summary to stdout.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# Add project root to sys.path so shared/ imports work
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.ast.project import find_project_by_repo_id  # noqa: E402
from shared.ast.parser_utils import ensure_parsers  # noqa: E402
from shared.ast.scanner import scan_and_parse, SYNC_BATCH_SIZE  # noqa: E402

SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: manon-scan.py <repo_id>"}))
        sys.exit(1)

    repo_id = sys.argv[1]

    # Look up project
    found = find_project_by_repo_id(repo_id)
    if not found:
        print(json.dumps({"error": f"repo_id {repo_id} not found in projects.json"}))
        sys.exit(1)

    project_path, info = found
    old_hashes = info.get("file_hashes", {})

    # Ensure tree-sitter parsers are installed
    ensure_parsers(project_path)

    # Scan and parse all changed files
    file_results, deleted, new_hashes = scan_and_parse(
        project_path, old_hashes, max_files=0,
    )

    total_files = len(file_results)
    deleted_files = len(deleted)
    total_batches = max(math.ceil(total_files / SYNC_BATCH_SIZE), 1) if (total_files or deleted) else 0

    # Write cache to disk
    SCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = SCAN_CACHE_DIR / f"{repo_id}.json"
    cache_data = {
        "file_results": file_results,
        "deleted": deleted,
        "new_hashes": new_hashes,
        "old_hashes": dict(old_hashes),
        "total_batches": total_batches,
        "project_path": project_path,
    }
    cache_file.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

    # Output summary to stdout
    summary = {
        "total_files": total_files,
        "deleted_files": deleted_files,
        "total_batches": total_batches,
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
