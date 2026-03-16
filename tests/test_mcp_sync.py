"""Tests for manon_mcp/_sync.py — sync progress, state tracking."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from manon_mcp._sync import (
    _write_sync_progress, _read_sync_progress, _is_syncing,
    _bg_sync_lock, _bg_sync_threads, INLINE_SCAN_LIMIT,
)


class TestSyncProgress:
    def test_write_and_read(self, tmp_path, monkeypatch):
        progress_file = tmp_path / "sync_progress.json"
        monkeypatch.setattr("manon_mcp._sync._SYNC_PROGRESS_FILE", progress_file)
        _write_sync_progress("repo1", "syncing", "50% done")
        result = _read_sync_progress("repo1")
        assert result is not None
        assert result["status"] == "syncing"
        assert result["message"] == "50% done"
        assert "updated_at" in result

    def test_read_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("manon_mcp._sync._SYNC_PROGRESS_FILE", tmp_path / "nope.json")
        assert _read_sync_progress("repo1") is None

    def test_write_multiple_repos(self, tmp_path, monkeypatch):
        progress_file = tmp_path / "sync_progress.json"
        monkeypatch.setattr("manon_mcp._sync._SYNC_PROGRESS_FILE", progress_file)
        _write_sync_progress("repo1", "done", "ok")
        _write_sync_progress("repo2", "syncing", "in progress")
        r1 = _read_sync_progress("repo1")
        r2 = _read_sync_progress("repo2")
        assert r1["status"] == "done"
        assert r2["status"] == "syncing"

    def test_overwrite_progress(self, tmp_path, monkeypatch):
        progress_file = tmp_path / "sync_progress.json"
        monkeypatch.setattr("manon_mcp._sync._SYNC_PROGRESS_FILE", progress_file)
        _write_sync_progress("repo1", "syncing", "50%")
        _write_sync_progress("repo1", "done", "100%")
        result = _read_sync_progress("repo1")
        assert result["status"] == "done"


class TestIsSyncing:
    def test_no_thread(self):
        _bg_sync_threads.clear()
        assert not _is_syncing("repo1")

    def test_dead_thread(self):
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        _bg_sync_threads["repo1"] = mock_thread
        assert not _is_syncing("repo1")
        _bg_sync_threads.clear()

    def test_alive_thread(self):
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        _bg_sync_threads["repo1"] = mock_thread
        assert _is_syncing("repo1")
        _bg_sync_threads.clear()


class TestConstants:
    def test_inline_scan_limit(self):
        assert INLINE_SCAN_LIMIT == 50
