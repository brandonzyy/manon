"""Tests for repo CRUD helpers (formerly application.mcp_sync_service)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from manon_mcp.tools.repo_crud import create_repo, delete_repo, get_repo, scan_files, upload_batch


def test_create_repo_with_local_path(monkeypatch, tmp_path):
    recorded: dict[str, object] = {}

    monkeypatch.setattr("manon_mcp.tools.repo_crud.count_scannable_files", lambda path: 7)
    monkeypatch.setattr(
        "manon_mcp.tools.repo_crud.set_project",
        lambda path, info: recorded.update({"path": path, "info": info}),
    )

    client = SimpleNamespace(_post=lambda path, payload: {"id": "repo123", "name": payload["name"]})
    result = create_repo(name="demo", branch="main", local_path=str(tmp_path), client=client)

    assert "repo123" in result
    assert "7" in result
    assert recorded["path"] == str(Path(tmp_path).resolve())


def test_get_repo_returns_json():
    client = SimpleNamespace(_get=lambda path: {"id": "repo123", "name": "demo"})
    result = get_repo(repo_id="repo123", client=client)
    assert json.loads(result)["id"] == "repo123"


def test_delete_repo_clears_local_binding(monkeypatch):
    saved: dict[str, object] = {}
    client_calls: list[str] = []

    monkeypatch.setattr("manon_mcp.tools.repo_crud.find_project_by_repo_id", lambda repo_id: ("C:/demo", {}))
    monkeypatch.setattr("manon_mcp.tools.repo_crud.load_projects", lambda: {"projects": {"C:/demo": {}}})
    monkeypatch.setattr("manon_mcp.tools.repo_crud.save_projects", lambda data: saved.update(data))

    client = SimpleNamespace(_delete=lambda path: client_calls.append(path))
    result = delete_repo(repo_id="repo123", client=client)

    assert "repo123" in result
    assert "C:/demo" not in saved["projects"]
    assert client_calls == ["/api/v1/repos/repo123"]


def test_scan_files_wraps_result():
    sync_module = SimpleNamespace(scan_files=lambda repo_id: {"status": "ok", "repo_id": repo_id})
    payload = json.loads(scan_files(repo_id="repo123", sync_module=sync_module))
    assert payload["repo_id"] == "repo123"


def test_upload_batch_wraps_error():
    sync_module = SimpleNamespace(upload_next_batch=lambda repo_id: (_ for _ in ()).throw(RuntimeError("bad batch")))
    payload = json.loads(upload_batch(repo_id="repo123", sync_module=sync_module))
    assert payload["status"] == "error"
    assert "bad batch" in payload["message"]
