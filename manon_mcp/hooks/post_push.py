#!/usr/bin/env python3
"""Manon post-push hook — update knowledge graph + print health score.

Usage: python post_push.py <project_path>

Designed to run in background after git push (invoked by pre-push hook).
Reads ~/.manon/projects.json to find repo_id, scans changed files,
uploads AST to server, then fetches and prints health score.
Writes results to ~/.manon/update_status.json for LLM feedback.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path

# Add project root to path so shared modules are importable
_root = str(Path(__file__).resolve().parents[2])
if _root not in sys.path:
    sys.path.insert(0, _root)


PROJECTS_FILE = Path.home() / ".manon" / "projects.json"
STATUS_FILE = Path.home() / ".manon" / "update_status.json"
CONFIG_FILE = Path.home() / ".manon" / "config.json"
SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"
SYNC_BATCH_SIZE = 50


def _load_config() -> dict:
    """Load persisted API config from ~/.manon/config.json."""
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        return {"projects": {}}
    return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))


def _find_repo_id(project_path: str) -> tuple[str, dict] | None:
    data = _load_projects()
    norm = str(Path(project_path).resolve())
    for path, info in data.get("projects", {}).items():
        if str(Path(path).resolve()) == norm:
            return info.get("repo_id", ""), info
    return None


def _api_url() -> str:
    url = os.environ.get("MANON_API_URL") or os.environ.get("MANON_API_URL_CN")
    if not url:
        url = _load_config().get("api_url", "")
    return url or "http://saas.matrixone.online:3700"

def _headers() -> dict:
    key = os.environ.get("MANON_API_KEY", "")
    if not key:
        key = _load_config().get("api_key", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _write_status(ok: bool, message: str) -> None:
    """Write result to status file for LLM to pick up on next tool call."""
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps({
            "ok": ok,
            "message": message,
            "timestamp": datetime.datetime.now().isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _wait_for_api(api_url: str, headers: dict, *, timeout: float = 45.0, interval: float = 3.0) -> tuple[bool, str]:
    """Wait for the API to come back before syncing graph data."""
    import httpx

    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with httpx.Client(base_url=api_url, headers=headers, timeout=5) as c:
                response = c.get("/health")
                response.raise_for_status()
            return True, ""
        except Exception as exc:
            last_error = str(exc)
            time.sleep(interval)
    return False, last_error


def _upload_ast_batches(file_results, deleted, repo_id, api_url, headers):
    """Upload AST data in batches. Raises on HTTP error."""
    import httpx
    for i in range(0, max(len(file_results), 1), SYNC_BATCH_SIZE):
        batch = file_results[i:i + SYNC_BATCH_SIZE]
        payload = {
            "files": batch,
            "deleted_files": deleted if i == 0 else [],
            "full_reindex": False,
        }
        with httpx.Client(base_url=api_url, headers=headers, timeout=45) as c:
            r = c.post(f"/api/v1/repos/{repo_id}/sync-ast", json=payload)
            r.raise_for_status()


def _sync_local_hashes(info, file_results, deleted, new_hashes, old_hashes, repo_id, api_url, headers):
    """Sync file hashes to info dict from server or locally-computed fallback."""
    import httpx
    partial_hashes = dict(old_hashes)
    for f in file_results:
        rp = f["rel_path"]
        if rp in new_hashes:
            partial_hashes[rp] = new_hashes[rp]
    for d in deleted:
        partial_hashes.pop(d, None)
    try:
        with httpx.Client(base_url=api_url, headers=headers, timeout=10) as c:
            r2 = c.get(f"/api/v1/repos/{repo_id}/index-status")
            r2.raise_for_status()
            server_stats = r2.json().get("stats") or {}
            server_hashes = server_stats.get("file_hashes")
            info["file_hashes"] = server_hashes if server_hashes is not None else partial_hashes
    except Exception:
        info["file_hashes"] = partial_hashes


def _sync_ast_changes(repo_id, info, project_path, api_url, headers):
    """Scan and upload AST changes. Returns (sync_ok, summary_parts)."""
    summary_parts = []
    sync_ok = False
    print("[manon] 扫描变更文件...")
    try:
        from core.ast import scan_and_parse, set_project
        old_hashes = info.get("file_hashes", {})
        stats_cache_file = SCAN_CACHE_DIR / f"{repo_id}_stats.json"
        try:
            stat_cache: dict = json.loads(stats_cache_file.read_text(encoding="utf-8"))
        except Exception:
            stat_cache = {}
        file_results, deleted, new_hashes = scan_and_parse(
            project_path, old_hashes, max_files=200, stat_cache=stat_cache,
        )
        try:
            SCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            stats_cache_file.write_text(json.dumps(stat_cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        if file_results or deleted:
            changed_names = [f["rel_path"] for f in file_results]
            print(f"[manon] 变更 {len(file_results)} 个文件: {', '.join(changed_names[:5])}"
                  + (" 等" if len(changed_names) > 5 else ""))
            if deleted:
                print(f"[manon] 删除 {len(deleted)} 个文件: {', '.join(deleted[:5])}"
                      + (" 等" if len(deleted) > 5 else ""))
            print("[manon] 上传 AST 并更新知识图谱...")
            _upload_ast_batches(file_results, deleted, repo_id, api_url, headers)
            msg = f"图谱已更新（{len(file_results)} 个文件重建 AST"
            if deleted:
                msg += f", {len(deleted)} 个文件移除"
            msg += "）"
            print(f"[manon] {msg}")
            summary_parts.append(msg)
            _sync_local_hashes(info, file_results, deleted, new_hashes, old_hashes, repo_id, api_url, headers)
            info["last_sync"] = datetime.datetime.now().isoformat()
            set_project(project_path, info)
            sync_ok = True
        else:
            print("[manon] 无文件变更，图谱已是最新")
            summary_parts.append("无文件变更")
            sync_ok = True
    except Exception as e:
        print(f"[manon] FAIL 图谱更新失败: {e}")
        summary_parts.append(f"同步失败: {e}")
    return sync_ok, summary_parts


def _fetch_health_score(repo_id, api_url, headers) -> str | None:
    """Fetch code health score. Returns formatted string or None on error."""
    import httpx
    try:
        with httpx.Client(base_url=api_url, headers=headers, timeout=60) as c:
            r = c.get(f"/api/v1/repos/{repo_id}/code-health")
            r.raise_for_status()
            health = r.json()
        score = health.get("score", 0)
        grade = health.get("grade", "?")
        print(f"[manon] => 代码健康: {score}/100 ({grade})")
        return f"健康评分: {score}/100 ({grade})"
    except Exception as e:
        print(f"[manon] FAIL 健康评分获取失败: {e}")
        return None


def _contract_delta(repo_id: str, project_path: str) -> str:
    """Print only what the push added.

    A gate that reprints every known dead surface on every push is a gate people
    learn to scroll past, so the baseline is what makes this usable: the first
    run records the existing debt silently, and later runs speak only when the
    push made it worse.
    """
    try:
        from core.contract_audit import audit_project
        from core.contract_audit.report import (
            diff_baseline,
            load_baseline,
            render_delta,
            save_baseline,
        )

        result = audit_project(project_path)
        baseline = load_baseline(repo_id)
        message = render_delta(result, baseline)
        new_findings, fixed = diff_baseline(result, baseline) if baseline else ([], [])
        save_baseline(repo_id, result)
        if message:
            print(message)
        if new_findings:
            return f"契约对账: 本次新增 {len(new_findings)} 个死面/待确认"
        if fixed:
            return f"契约对账: {len(fixed)} 个旧死面已消失"
        return ""
    except Exception as exc:
        print(f"[manon] 契约对账跳过: {exc}")
        return ""


def main():
    for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(_k, None)

    if len(sys.argv) < 2:
        print("[manon] usage: post_push.py <project_path>")
        return

    project_path = sys.argv[1]
    result = _find_repo_id(project_path)
    if not result:
        print(f"[manon] 项目未注册: {project_path}")
        return

    repo_id, info = result
    if not repo_id:
        print("[manon] 未找到 repo_id，请重新运行 manon_init。")
        return

    api_url = _api_url()
    headers = _headers()

    if "Bearer " == headers.get("Authorization", "").strip():
        print("[manon] MANON_API_KEY 未配置，跳过图谱同步。请重新运行 manon_setup_hooks。")
        _write_status(False, "API key 未配置")
        return

    ready, wait_error = _wait_for_api(api_url, headers)
    if not ready:
        message = f"API unavailable after restart: {wait_error}"
        print(f"[manon] FAIL {message}")
        _write_status(False, message)
        return

    sync_ok, summary_parts = _sync_ast_changes(repo_id, info, project_path, api_url, headers)

    print("[manon] 计算代码健康评分...")
    health_msg = _fetch_health_score(repo_id, api_url, headers)
    if health_msg:
        summary_parts.append(health_msg)

    contract_msg = _contract_delta(repo_id, project_path)
    if contract_msg:
        summary_parts.append(contract_msg)

    _write_status(sync_ok, " | ".join(summary_parts))


if __name__ == "__main__":
    main()
