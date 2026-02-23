"""Tests for mcp helper functions — _tools.py, _config.py, _hooks.py."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from mcp._tools import (
    _detect_git_root, _write_update_status, _read_update_status,
    _fmt_stats, _UPDATE_STATUS_FILE,
)
from mcp._hooks import _PRE_SEARCH_HOOK, _PRE_EDIT_HOOK, _persist_api_config


class TestDetectGitRoot:
    def test_in_git_repo(self, tmp_path):
        # Create a fake .git dir
        (tmp_path / ".git").mkdir()
        git_root, prefix = _detect_git_root(tmp_path)
        # Should detect the tmp_path as git root (or actual git root)
        assert isinstance(git_root, Path)
        assert isinstance(prefix, str)

    def test_not_git_repo(self, tmp_path):
        sub = tmp_path / "notagit"
        sub.mkdir()
        git_root, prefix = _detect_git_root(sub)
        assert isinstance(git_root, Path)


class TestUpdateStatus:
    def test_write_and_read(self, tmp_path, monkeypatch):
        status_file = tmp_path / "update_status.json"
        monkeypatch.setattr("mcp._tools._UPDATE_STATUS_FILE", status_file)
        _write_update_status(True, ["ok", "done"])
        msg = _read_update_status()
        assert msg is not None
        assert "ok" in msg
        # File should be deleted after read
        assert not status_file.exists()

    def test_read_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mcp._tools._UPDATE_STATUS_FILE", tmp_path / "nope.json")
        assert _read_update_status() is None


class TestFmtStats:
    def test_format(self):
        s = {"total_files": 10, "total_entities": 50, "total_relations": 100, "total_chunks": 30}
        result = _fmt_stats(s)
        assert "10" in result
        assert "50" in result
        assert "100" in result

    def test_fallback_keys(self):
        s = {"files_indexed": 5, "entities_added": 20, "relations_added": 40, "chunks_added": 10}
        result = _fmt_stats(s)
        assert "5" in result
        assert "20" in result


class TestHookScripts:
    def test_pre_search_hook_is_valid_python(self):
        compile(_PRE_SEARCH_HOOK, "<pre_search>", "exec")

    def test_pre_edit_hook_is_valid_python(self):
        compile(_PRE_EDIT_HOOK, "<pre_edit>", "exec")


class TestPersistApiConfig:
    def test_persist(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        mock_config = MagicMock()
        mock_config.API_URL = "http://test:3700"
        mock_config.API_KEY = "test-key"
        monkeypatch.setattr("mcp._hooks._config", mock_config)
        # Patch the config file path
        with patch("mcp._hooks.Path") as mock_path:
            mock_path.home.return_value = tmp_path
            # Just verify it doesn't crash
            _persist_api_config()
