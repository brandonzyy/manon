#!/usr/bin/env python3
"""Manon post-push hook — update knowledge graph + print health score.

Usage: python post_push.py <project_path>

Designed to run in background after git push (invoked by pre-push hook).
Reads ~/.manon/projects.json to find repo_id, scans changed files,
uploads AST to server, then fetches and prints health score.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Add project root to path so shared modules are importable
_root = str(Path(__file__).resolve().parents[2])
if _root not in sys.path:
    sys.path.insert(0, _root)


PROJECTS_FILE = Path.home() / ".manon" / "projects.json"
SYNC_BATCH_SIZE = 50


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
    return os.environ.get("MANON_API_URL", os.environ.get("MANON_API_URL_CN", "http://117.131.45.179:3700"))


def _headers() -> dict:
    key = os.environ.get("MANON_API_KEY", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
def main():
    if len(sys.argv) < 2:
        print("[manon] usage: post_push.py <project_path>")
        return

    project_path = sys.argv[1]
    result = _find_repo_id(project_path)
    if not result:
        print(f"[manon] 项目未注册: {project_path}")
        print("[manon] 请先运行 manon_init 初始化项目。")
        return

    repo_id, info = result
    if not repo_id:
        print("[manon] 未找到 repo_id，请重新运行 manon_init。")
        return

    import httpx
    api_url = _api_url()
    headers = _headers()

    # Step 1: Scan and upload AST changes
    print(f"[manon] 正在扫描变更文件...")
    try:
        from shared.ast_sync import scan_and_parse, set_project
        old_hashes = info.get("file_hashes", {})
        file_results, deleted, new_hashes = scan_and_parse(
            project_path, old_hashes, max_files=200,
        )
        if file_results or deleted:
            # Upload in batches
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
            print(f"[manon] 已同步 {len(file_results)} 文件, 删除 {len(deleted)} 文件。")

            # Update local project registry
            info["file_hashes"] = new_hashes
            import datetime
            info["last_sync"] = datetime.datetime.now().isoformat()
            set_project(project_path, info)
        else:
            print("[manon] 无文件变更。")
    except Exception as e:
        print(f"[manon] AST 同步失败: {e}")

    # Step 2: Fetch health score
    print("[manon] 正在计算代码健康评分...")
    try:
        with httpx.Client(base_url=api_url, headers=headers, timeout=60) as c:
            r = c.get(f"/api/v1/repos/{repo_id}/code-health")
            r.raise_for_status()
            health = r.json()

        score = health.get("score", 0)
        grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"
        print(f"\n[manon] 代码健康: {score}/100 ({grade})")
        for d in health.get("dimensions", []):
            bar = "█" * d["value"] + "░" * (10 - d["value"])
            print(f"  {d['abbr']:>2s} {d['name']:<6s} {bar} {d['value']}/10")
        print()
    except Exception as e:
        print(f"[manon] 健康评分获取失败: {e}")


if __name__ == "__main__":
    main()
