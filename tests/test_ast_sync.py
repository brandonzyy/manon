"""Tests for shared/ast_sync — project registry, language detection, config."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from shared.ast_sync import (
    load_projects, save_projects, get_project, set_project,
    find_project_by_repo_id, detect_languages, count_scannable_files,
    set_custom_excludes, SYNC_BATCH_SIZE, _ALWAYS_EXCLUDE,
)


class TestProjectRegistry:
    def test_load_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shared.ast_sync.PROJECTS_FILE", tmp_path / "nope.json")
        data = load_projects()
        assert data == {"projects": {}}

    def test_save_and_load(self, tmp_path, monkeypatch):
        pf = tmp_path / "projects.json"
        monkeypatch.setattr("shared.ast_sync.PROJECTS_FILE", pf)
        monkeypatch.setattr("shared.ast_sync.PROJECTS_DIR", tmp_path)
        data = {"projects": {"path1": {"repo_id": "r1", "name": "test"}}}
        save_projects(data)
        loaded = load_projects()
        assert loaded["projects"]["path1"]["repo_id"] == "r1"

    def test_get_project_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shared.ast_sync.PROJECTS_FILE", tmp_path / "nope.json")
        assert get_project("/nonexistent") is None

    def test_set_and_get_project(self, tmp_path, monkeypatch):
        pf = tmp_path / "projects.json"
        monkeypatch.setattr("shared.ast_sync.PROJECTS_FILE", pf)
        monkeypatch.setattr("shared.ast_sync.PROJECTS_DIR", tmp_path)
        set_project(str(tmp_path), {"repo_id": "r1", "name": "test"})
        proj = get_project(str(tmp_path))
        assert proj is not None
        assert proj["repo_id"] == "r1"

    def test_find_project_by_repo_id(self, tmp_path, monkeypatch):
        pf = tmp_path / "projects.json"
        monkeypatch.setattr("shared.ast_sync.PROJECTS_FILE", pf)
        monkeypatch.setattr("shared.ast_sync.PROJECTS_DIR", tmp_path)
        set_project(str(tmp_path), {"repo_id": "abc", "name": "test"})
        result = find_project_by_repo_id("abc")
        assert result is not None
        path, info = result
        assert info["repo_id"] == "abc"

    def test_find_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shared.ast_sync.PROJECTS_FILE", tmp_path / "nope.json")
        assert find_project_by_repo_id("nonexistent") is None


class TestDetectLanguages:
    def test_python_project(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "util.py").write_text("x = 1")
        langs = detect_languages(str(tmp_path))
        assert "python" in langs

    def test_empty_dir(self, tmp_path):
        langs = detect_languages(str(tmp_path))
        assert len(langs) == 0

    def test_mixed_project(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "util.py").write_text("y = 2")
        langs = detect_languages(str(tmp_path))
        assert "python" in langs


class TestCountScannableFiles:
    def test_count(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.py").write_text("y = 2")
        (tmp_path / "readme.md").write_text("# hi")
        count = count_scannable_files(str(tmp_path))
        assert count >= 2


class TestConstants:
    def test_batch_size(self):
        assert SYNC_BATCH_SIZE == 50

    def test_always_exclude(self):
        assert "**/.git/**" in _ALWAYS_EXCLUDE
        assert "**/node_modules/**" in _ALWAYS_EXCLUDE


class TestSetCustomExcludes:
    def test_set_excludes(self, tmp_path, monkeypatch):
        pf = tmp_path / "projects.json"
        monkeypatch.setattr("shared.ast_sync.PROJECTS_FILE", pf)
        monkeypatch.setattr("shared.ast_sync.PROJECTS_DIR", tmp_path)
        set_project(str(tmp_path), {"repo_id": "r1", "name": "test"})
        set_custom_excludes(str(tmp_path), ["**/data/**"])
        proj = get_project(str(tmp_path))
        assert "**/data/**" in proj.get("custom_excludes", [])
