"""Manon MCP — background sync worker and progress tracking."""
from __future__ import annotations

import datetime
import json
import logging
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
    while True:
        file_results, deleted, new_hashes = _scan(
            project_path, current_hashes,
            max_files=max_files,
        )
        if not file_results and not deleted:
            break
        n = len(file_results)
        _write_sync_progress(
            repo_id, "syncing",
            f"上传 {n} 文件... (累计 {total_synced + n})",
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
        _write_sync_progress(repo_id, "syncing", "扫描文件中...")
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
                   max_files: int = -1, full_reindex: bool = False) -> str:
    """Start background sync if not already running. Returns status message.

    Args:
        max_files: -1 = use default limit, 0 = unlimited (full reindex)
    """
    if max_files == -1:
        max_files = INLINE_SCAN_LIMIT
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
