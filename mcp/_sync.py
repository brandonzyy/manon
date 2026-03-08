"""Manon MCP — background sync worker and progress tracking."""
from __future__ import annotations

import datetime
import json
import logging
import math
import threading
from pathlib import Path

from shared.ast_sync import (
    scan_and_parse, find_project_by_repo_id, set_project,
    SYNC_BATCH_SIZE,
)

log = logging.getLogger("manon-mcp")

# ── Injected dependencies ────────────────────────────
_client = None  # _client module
INLINE_SCAN_LIMIT = 50


def init(client, constants):
    """Inject dependencies from server.py."""
    global _client, INLINE_SCAN_LIMIT
    _client = client
    INLINE_SCAN_LIMIT = constants["INLINE_SCAN_LIMIT"]


# ── Sync state ───────────────────────────────────────
_bg_sync_lock = threading.Lock()
_bg_sync_threads: dict[str, threading.Thread] = {}
_SYNC_PROGRESS_FILE = Path.home() / ".manon" / "sync_progress.json"

# ── Scan cache (for scan + upload_batch mode) ────────
_scan_cache: dict[str, dict] = {}  # repo_id → {file_results, deleted, new_hashes, cursor, total_batches, ...}


def _write_sync_progress(repo_id: str, status: str, message: str):
    """Write sync progress to JSON file (thread-safe via lock)."""
    try:
        _SYNC_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _bg_sync_lock:
            data = {}
            if _SYNC_PROGRESS_FILE.exists():
                try:
                    data = json.loads(_SYNC_PROGRESS_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass
            data[repo_id] = {
                "status": status,
                "message": message,
                "updated_at": datetime.datetime.now().isoformat(),
            }
            _SYNC_PROGRESS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
    except Exception as e:
        log.warning("Failed to write sync progress: %s", e)


def _read_sync_progress(repo_id: str) -> dict | None:
    """Read sync progress for a repo. Returns dict with status/message or None."""
    try:
        if _SYNC_PROGRESS_FILE.exists():
            data = json.loads(_SYNC_PROGRESS_FILE.read_text(encoding="utf-8"))
            return data.get(repo_id)
    except Exception:
        pass
    return None


def _is_syncing(repo_id: str) -> bool:
    """Check if a background sync thread is running for this repo."""
    with _bg_sync_lock:
        t = _bg_sync_threads.get(repo_id)
        return t is not None and t.is_alive()


def _sync_to_server(repo_id: str, file_results: list, deleted_files: list,
                    full_reindex: bool = False) -> dict:
    """Upload AST data to server in batches."""
    last_result = {}
    for i in range(0, max(len(file_results), 1), SYNC_BATCH_SIZE):
        batch = file_results[i:i + SYNC_BATCH_SIZE]
        payload = {
            "files": batch,
            "deleted_files": deleted_files if i == 0 else [],
            "full_reindex": full_reindex and i == 0,
        }
        last_result = _client._post(f"/api/v1/repos/{repo_id}/sync-ast", payload)
    return last_result


def _run_sync_loop(repo_id, project_path, current_hashes, max_files, full_reindex,
                   _scan, _find_project, _set_project):
    """Inner sync loop: scan → upload → update hashes. Returns (total_synced, total_deleted).

    Args:
        max_files: 0 = unlimited, >0 = limit per batch
    """
    total_synced = 0
    total_deleted = 0
    batch_num = 0
    while True:
        batch_num += 1
        _write_sync_progress(
            repo_id, "syncing",
            f"扫描项目文件 (第 {batch_num} 轮)...",
        )
        file_results, deleted, new_hashes = _scan(
            project_path, current_hashes,
            max_files=max_files,
        )
        if not file_results and not deleted:
            break
        n = len(file_results)
        d = len(deleted)
        _write_sync_progress(
            repo_id, "syncing",
            f"上传第 {batch_num} 批 ({n} 文件, {d} 删除)... 累计 {total_synced + n} 文件",
        )
        _sync_to_server(repo_id, file_results, deleted, full_reindex=full_reindex)
        full_reindex = False
        if max_files == 0:
            current_hashes.clear()
            current_hashes.update(new_hashes)
        else:
            for f in file_results:
                rp = f["rel_path"]
                if rp in new_hashes:
                    current_hashes[rp] = new_hashes[rp]
            for d in deleted:
                current_hashes.pop(d, None)
        total_synced += len(file_results)
        total_deleted += len(deleted)
        found = _find_project(repo_id)
        if found:
            lp, info = found
            info["file_hashes"] = current_hashes
            info["last_sync"] = datetime.datetime.now().isoformat()
            _set_project(lp, info)
        log.info("BG sync batch: +%d synced, +%d deleted, total=%d",
                 len(file_results), len(deleted), len(current_hashes))
    return total_synced, total_deleted


def _bg_sync_worker(repo_id: str, project_path: str, old_hashes: dict,
                    max_files: int, full_reindex: bool):
    """Background thread: scan files, upload AST, loop until complete."""
    try:
        _write_sync_progress(repo_id, "syncing", "扫描项目文件...")
        current_hashes = dict(old_hashes)
        total_synced, total_deleted = _run_sync_loop(
            repo_id, project_path, current_hashes, max_files, full_reindex,
            scan_and_parse, find_project_by_repo_id, set_project,
        )
        _write_sync_progress(
            repo_id, "done",
            f"完成: {total_synced} 文件同步, {total_deleted} 文件删除",
        )
        log.info("BG sync done for %s: %d synced, %d deleted",
                 repo_id, total_synced, total_deleted)
    except Exception as e:
        log.error("BG sync error for %s: %s", repo_id, e)
        _write_sync_progress(repo_id, "error", str(e))
    finally:
        with _bg_sync_lock:
            _bg_sync_threads.pop(repo_id, None)


def _start_bg_sync(repo_id: str, project_path: str, old_hashes: dict,
                   max_files: int = -1, full_reindex: bool = False,
                   wait: bool = False) -> str:
    """Start sync. Returns status message.

    Args:
        max_files: -1 = use default limit, 0 = unlimited (full reindex)
        wait: if True, run synchronously and block until complete
    """
    if max_files == -1:
        max_files = INLINE_SCAN_LIMIT

    if wait:
        return _run_sync_foreground(repo_id, project_path, old_hashes,
                                    max_files, full_reindex)

    if _is_syncing(repo_id):
        prog = _read_sync_progress(repo_id)
        msg = prog["message"] if prog else "进行中"
        return f"后台同步进行中: {msg}"

    t = threading.Thread(
        target=_bg_sync_worker,
        args=(repo_id, project_path, old_hashes, max_files, full_reindex),
        daemon=True,
    )
    with _bg_sync_lock:
        _bg_sync_threads[repo_id] = t
    t.start()
    return "后台同步已启动，用 manon_index_status 查看进度。"


def _run_sync_foreground(repo_id: str, project_path: str, old_hashes: dict,
                         max_files: int, full_reindex: bool) -> str:
    """Run sync synchronously (blocking). Returns completion message."""
    try:
        _write_sync_progress(repo_id, "syncing", "扫描项目文件...")
        current_hashes = dict(old_hashes)
        total_synced, total_deleted = _run_sync_loop(
            repo_id, project_path, current_hashes, max_files, full_reindex,
            scan_and_parse, find_project_by_repo_id, set_project,
        )
        msg = f"完成: {total_synced} 文件同步, {total_deleted} 文件删除"
        _write_sync_progress(repo_id, "done", msg)
        log.info("Foreground sync done for %s: %d synced, %d deleted",
                 repo_id, total_synced, total_deleted)
        return f"✅ 同步完成: {msg}"
    except Exception as e:
        log.error("Foreground sync error for %s: %s", repo_id, e)
        _write_sync_progress(repo_id, "error", str(e))
        return f"❌ 同步失败: {e}"


# ── Scan + Upload Batch mode ────────────────────────

UPLOAD_BATCH_SIZE = SYNC_BATCH_SIZE  # files per upload_batch call


def scan_files(repo_id: str, project_path: str, old_hashes: dict) -> dict:
    """Scan project files and cache results for subsequent upload_batch calls.

    Returns:
        {total_files, deleted_files, total_batches}
    """
    file_results, deleted, new_hashes = scan_and_parse(
        project_path, old_hashes, max_files=0,
    )
    total_files = len(file_results)
    total_batches = max(math.ceil(total_files / UPLOAD_BATCH_SIZE), 1) if (total_files or deleted) else 0

    _scan_cache[repo_id] = {
        "file_results": file_results,
        "deleted": deleted,
        "new_hashes": new_hashes,
        "old_hashes": dict(old_hashes),
        "cursor": 0,
        "total_batches": total_batches,
        "project_path": project_path,
    }
    return {
        "total_files": total_files,
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

    _sync_to_server(repo_id, batch_files, batch_deleted)

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
