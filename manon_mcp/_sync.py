"""Manon MCP — scan cache loader and batch uploader.

Heavy scanning is done by scripts/manon-scan.py (external process).
This module only loads cached results and uploads them in batches.
"""
from __future__ import annotations

import datetime
import json
import logging
import math
import threading
from pathlib import Path

from core.ast import (
    find_project_by_repo_id, set_project,
    SYNC_BATCH_SIZE,
)

# ── Scan cache on disk (written by scripts/manon-scan.py) ─
SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"
_SYNC_PROGRESS_FILE = Path.home() / ".manon" / "sync_progress.json"

log = logging.getLogger("manon-mcp")

# ── Injected dependencies ────────────────────────────
_client = None  # _client module


def init(client):
    """Inject dependencies from server.py."""
    global _client
    _client = client


# ── Scan cache (for scan + upload_batch mode) ────────
_scan_cache: dict[str, dict] = {}  # repo_id → {file_results, deleted, new_hashes, cursor, total_batches, ...}
_bg_sync_lock = threading.Lock()
_bg_sync_threads: dict[str, object] = {}

UPLOAD_BATCH_SIZE = SYNC_BATCH_SIZE  # files per upload_batch call
INLINE_SCAN_LIMIT = 50


def _write_sync_progress(repo_id: str, status: str, message: str) -> None:
    """Persist lightweight sync status for compatibility with existing tooling."""
    with _bg_sync_lock:
        data: dict[str, dict] = {}
        if _SYNC_PROGRESS_FILE.exists():
            try:
                data = json.loads(_SYNC_PROGRESS_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[repo_id] = {
            "status": status,
            "message": message,
            "updated_at": datetime.datetime.now().isoformat(),
        }
        _SYNC_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SYNC_PROGRESS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _read_sync_progress(repo_id: str) -> dict | None:
    """Read persisted sync progress for a repo if present."""
    try:
        if not _SYNC_PROGRESS_FILE.exists():
            return None
        data = json.loads(_SYNC_PROGRESS_FILE.read_text(encoding="utf-8"))
        result = data.get(repo_id)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _is_syncing(repo_id: str) -> bool:
    """Return whether a background sync thread is currently alive."""
    thread = _bg_sync_threads.get(repo_id)
    if not thread:
        return False
    alive = bool(getattr(thread, "is_alive", lambda: False)())
    if not alive:
        _bg_sync_threads.pop(repo_id, None)
    return alive


def scan_files(repo_id: str) -> dict:
    """Load scan results from disk cache (written by scripts/manon-scan.py).

    Reads ~/.manon/scan_cache/<repo_id>.json into memory _scan_cache,
    then removes the disk file.

    Returns:
        {total_files, deleted_files, total_batches}
    """
    cache_file = SCAN_CACHE_DIR / f"{repo_id}.json"
    if not cache_file.exists():
        raise FileNotFoundError(
            f"No scan cache at {cache_file}. "
            "Run `<MANON_PYTHON> <MANON_DIR>/scripts/manon-scan.py {repo_id}` first (use the Python path from manon_init output)."
        )

    cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
    file_results = cache_data["file_results"]
    deleted = cache_data["deleted"]
    total_batches = cache_data["total_batches"]

    _scan_cache[repo_id] = {
        "file_results": file_results,
        "deleted": deleted,
        "new_hashes": cache_data["new_hashes"],
        "old_hashes": cache_data["old_hashes"],
        "cursor": 0,
        "total_batches": total_batches,
        "project_path": cache_data["project_path"],
    }

    # Clean up disk cache
    try:
        cache_file.unlink()
    except Exception:
        pass

    return {
        "total_files": len(file_results),
        "deleted_files": len(deleted),
        "total_batches": total_batches,
    }


def upload_next_batch(repo_id: str) -> dict:
    """Upload next batch from scan cache. Call repeatedly until status == 'done'.

    Returns:
        {batch, uploaded, remaining, total, deleted, status}
    """
    cache = _scan_cache.get(repo_id)
    if not cache:
        return {"status": "error", "message": "No scan cache. Call manon_scan_files first."}

    file_results = cache["file_results"]
    deleted = cache["deleted"]
    cursor = cache["cursor"]
    total_files = len(file_results)
    total_batches = cache["total_batches"]

    # Nothing to upload
    if not file_results and not deleted:
        _scan_cache.pop(repo_id, None)
        return {"batch": 0, "uploaded": 0, "remaining": 0, "total": 0, "deleted": 0, "status": "done"}

    start = cursor
    end = min(cursor + UPLOAD_BATCH_SIZE, total_files)
    batch_files = file_results[start:end]

    # Send deleted only in the first batch
    batch_deleted = deleted if cursor == 0 else []

    # Upload to server
    payload = {
        "files": batch_files,
        "deleted_files": batch_deleted,
        "full_reindex": False,
    }
    _client._post(f"/api/v1/repos/{repo_id}/sync-ast", payload)

    cache["cursor"] = end
    batch_num = math.ceil(end / UPLOAD_BATCH_SIZE) if end > 0 else 1
    remaining = total_files - end
    is_done = end >= total_files

    # Update file_hashes incrementally
    new_hashes = cache["new_hashes"]
    found = find_project_by_repo_id(repo_id)
    if found:
        lp, info = found
        current_hashes = info.get("file_hashes", {})
        # Apply this batch's hashes
        for f in batch_files:
            rp = f["rel_path"]
            if rp in new_hashes:
                current_hashes[rp] = new_hashes[rp]
        # Apply deletes (first batch only)
        if cursor == 0:
            for d in deleted:
                current_hashes.pop(d, None)
        if is_done:
            # Final batch: replace all hashes with new_hashes
            current_hashes = new_hashes
        info["file_hashes"] = current_hashes
        info["last_sync"] = datetime.datetime.now().isoformat()
        set_project(lp, info)

    if is_done:
        _scan_cache.pop(repo_id, None)
        log.info("Upload batch done for %s: %d synced, %d deleted",
                 repo_id, total_files, len(deleted))

    return {
        "batch": batch_num,
        "total_batches": total_batches,
        "uploaded": end,
        "remaining": remaining,
        "total": total_files,
        "deleted": len(deleted),
        "status": "done" if is_done else "uploading",
    }
