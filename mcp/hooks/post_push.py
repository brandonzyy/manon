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
from pathlib import Path

# Add project root to path so shared modules are importable
_root = str(Path(__file__).resolve().parents[2])
if _root not in sys.path:
    sys.path.insert(0, _root)


PROJECTS_FILE = Path.home() / ".manon" / "projects.json"
STATUS_FILE = Path.home() / ".manon" / "update_status.json"
CONFIG_FILE = Path.home() / ".manon" / "config.json"
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


def _sync_ast_changes(repo_id, info, project_path, api_url, headers):
    """Scan and upload AST changes. Returns (sync_ok, summary_parts)."""
    import httpx
    summary_parts = []
    sync_ok = False
    print("[manon] 扫描变更文件...")
    try:
        from shared.ast_sync import scan_and_parse, set_project
        old_hashes = info.get("file_hashes", {})
        file_results, deleted, new_hashes = scan_and_parse(
            project_path, old_hashes, max_files=200,
        )
        if file_results or deleted:
            changed_names = [f["rel_path"] for f in file_results]
            print(f"[manon] 变更 {len(file_results)} 个文件: {', '.join(changed_names[:5])}"
                  + (" 等" if len(changed_names) > 5 else ""))
            if deleted:
                print(f"[manon] 删除 {len(deleted)} 个文件: {', '.join(deleted[:5])}"
                      + (" 等" if len(deleted) > 5 else ""))
            print("[manon] 上传 AST 并更新知识图谱...")
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
            msg = f"图谱已更新（{len(file_results)} 个文件重建 AST"
            if deleted:
                msg += f", {len(deleted)} 个文件移除"
            msg += "）"
            print(f"[manon] {msg}")
            summary_parts.append(msg)
            partial_hashes = dict(old_hashes)
            for f in file_results:
                rp = f["rel_path"]
                if rp in new_hashes:
                    partial_hashes[rp] = new_hashes[rp]
            for d in deleted:
                partial_hashes.pop(d, None)
            info["file_hashes"] = partial_hashes
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


def main():
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

    import httpx
    api_url = _api_url()
    headers = _headers()

    if "Bearer " == headers.get("Authorization", "").strip():
        print("[manon] MANON_API_KEY 未配置，跳过图谱同步。请重新运行 manon_setup_hooks。")
        _write_status(False, "API key 未配置")
        return

    sync_ok, summary_parts = _sync_ast_changes(repo_id, info, project_path, api_url, headers)

    print("[manon] 计算代码健康评分...")
    try:
        with httpx.Client(base_url=api_url, headers=headers, timeout=60) as c:
            r = c.get(f"/api/v1/repos/{repo_id}/code-health")
            r.raise_for_status()
            health = r.json()
        score = health.get("score", 0)
        grade = health.get("grade", "?")
        summary_parts.append(f"健康评分: {score}/100 ({grade})")
        print(f"[manon] => 代码健康: {score}/100 ({grade})")
    except Exception as e:
        print(f"[manon] FAIL 健康评分获取失败: {e}")

    _write_status(sync_ok, " | ".join(summary_parts))


if __name__ == "__main__":
    main()
