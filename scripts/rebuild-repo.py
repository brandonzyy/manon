#!/usr/bin/env python3
"""Full-reindex repos on the Manon server.

Usage:
    python scripts/rebuild-repo.py <repo_id> [<repo_id> ...]

Clears the repo's local file hashes (so the scanner picks up every file),
runs the AST scan, then uploads all batches with full_reindex on the first
batch — the server drops the old graph/vectors/chunks and rebuilds from
scratch. Required after switching the server's embedding model: vectors
from different models are incompatible (dimension and space), so each repo
needs one full rebuild before its search works again.

Reads api_url/api_key from ~/.manon/config.json; updates file_hashes in
~/.manon/projects.json from the server's index-status on completion, so the
next incremental sync resumes normally.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_SCRIPT = ROOT / "skills" / "manon" / "scripts" / "manon-scan.py"
SCAN_CACHE_DIR = Path.home() / ".manon" / "scan_cache"
PROJECTS_FILE = Path.home() / ".manon" / "projects.json"
CONFIG_FILE = Path.home() / ".manon" / "config.json"
SYNC_BATCH_SIZE = 50  # must match core.ast.SYNC_BATCH_SIZE


def _http_json(method: str, url: str, api_key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"FAIL: {method} {url} -> HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}") from exc


def _venv_python() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def rebuild(repo_id: str, api_url: str, api_key: str) -> dict:
    raw = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    projects = raw["projects"]
    entry = next(((p, i) for p, i in projects.items() if i.get("repo_id") == repo_id), None)
    if not entry:
        raise SystemExit(f"FAIL: repo {repo_id} not found in {PROJECTS_FILE}")
    project_path, info = entry
    print(f"=== {repo_id} ({project_path}) ===")

    # 1. Clear local hashes so the scan sees every file as new.
    old_count = len(info.get("file_hashes", {}))
    info["file_hashes"] = {}
    PROJECTS_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  cleared {old_count} local file hashes")

    # 2. Full scan (external process — same one manon_init uses).
    t0 = time.monotonic()
    result = subprocess.run(
        [_venv_python(), str(SCAN_SCRIPT), repo_id],
        capture_output=True, text=True, timeout=1800,
    )
    if result.returncode != 0:
        raise SystemExit(f"FAIL: scan for {repo_id}:\n{result.stdout[-500:]}\n{result.stderr[-500:]}")
    print(f"  scan done in {time.monotonic() - t0:.0f}s: {result.stdout.strip().splitlines()[-1]}")

    cache_file = SCAN_CACHE_DIR / f"{repo_id}.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8"))
    cache_file.unlink()
    file_results, deleted = cache["file_results"], cache["deleted"]
    print(f"  uploading {len(file_results)} files in batches of {SYNC_BATCH_SIZE}")

    # 3. Upload; full_reindex only on the first batch wipes server-side state.
    total = len(file_results)
    for start in range(0, max(total, 1), SYNC_BATCH_SIZE):
        batch_files = file_results[start:start + SYNC_BATCH_SIZE]
        if not batch_files and not (start == 0 and deleted):
            break
        is_first, is_final = start == 0, start + SYNC_BATCH_SIZE >= total
        t1 = time.monotonic()
        _http_json("POST", f"{api_url}/api/v1/repos/{repo_id}/sync-ast", api_key, {
            "files": batch_files,
            "deleted_files": deleted if is_first else [],
            "full_reindex": is_first,
            "is_final_batch": is_final or total == 0,
        })
        print(f"  batch {start // SYNC_BATCH_SIZE + 1}: {min(start + SYNC_BATCH_SIZE, total)}/{total} "
              f"({time.monotonic() - t1:.1f}s)")

    # 4. Pull final stats + server-side hashes back into local state.
    status = _http_json("GET", f"{api_url}/api/v1/repos/{repo_id}/index-status", api_key)
    stats = status.get("stats") or {}
    info["file_hashes"] = stats.get("file_hashes") or cache["new_hashes"]
    info["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000")
    PROJECTS_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  done: entities={stats.get('total_entities')} relations={stats.get('total_relations')} "
          f"chunks={stats.get('total_chunks')} files={stats.get('total_files')}")
    return stats


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    api_url, api_key = cfg["api_url"].rstrip("/"), cfg["api_key"]
    for repo_id in sys.argv[1:]:
        rebuild(repo_id, api_url, api_key)


if __name__ == "__main__":
    main()
