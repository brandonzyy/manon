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
import os
import site
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"


def _find_project_root() -> Path:
    """Search upward for repo root (contains manon_mcp/). Works from any script location."""
    candidate = SCRIPT_DIR
    for _ in range(6):
        if (candidate / "manon_mcp").exists():
            return candidate
        candidate = candidate.parent
    return SCRIPT_DIR.parent  # fallback


PROJECT_ROOT = _find_project_root()
VENV_DIR = PROJECT_ROOT / ".venv"
REQ_FILE = PROJECT_ROOT / "manon_mcp" / "requirements.txt"


def _venv_site_packages() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return VENV_DIR / "lib" / version / "site-packages"


def _scan_runtime_ready() -> bool:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    # Try direct import first — succeeds when run via MANON_PYTHON (venv already has deps)
    try:
        import httpx  # noqa: F401
        import yaml  # noqa: F401
        return True
    except ImportError:
        pass

    # Fallback: activate local .venv
    site_packages = _venv_site_packages()
    if not site_packages.exists():
        return False
    site.addsitedir(str(site_packages))
    try:
        import httpx  # noqa: F401
        import yaml  # noqa: F401
    except Exception:
        return False
    return True


def _repair_scan_runtime() -> None:
    subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR), "--clear"],
        check=True,
        cwd=str(PROJECT_ROOT),
    )

    site_packages = _venv_site_packages()
    site_packages.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-q",
            "--disable-pip-version-check",
            "--upgrade",
            "--target",
            str(site_packages),
            "-r",
            str(REQ_FILE),
        ],
        check=True,
        cwd=str(PROJECT_ROOT),
    )


def _bootstrap_scan_runtime() -> None:
    if _scan_runtime_ready():
        return
    _repair_scan_runtime()
    if not _scan_runtime_ready():
        raise RuntimeError("failed to bootstrap scan runtime")


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: manon-scan.py <repo_id>"}))
        sys.exit(1)

    _bootstrap_scan_runtime()

    from core.ast.project import find_project_by_repo_id
    from core.ast.parser_utils import ensure_parsers
    from core.ast.scanner import SYNC_BATCH_SIZE, scan_and_parse

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
