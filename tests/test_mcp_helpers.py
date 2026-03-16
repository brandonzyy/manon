"""Tests for mcp helper functions — _tools.py, _config.py, _hooks.py."""
import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from manon_mcp._hooks import _PRE_SEARCH_HOOK, _PRE_EDIT_HOOK, _persist_api_config, _install_hook
from manon_mcp._tools import _write_update_status, _read_update_status, _UPDATE_STATUS_FILE
from manon_mcp.tools.impact import _detect_git_root, _find_changed_symbols
from manon_mcp.tools.init_helpers import _fmt_stats


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
        monkeypatch.setattr("manon_mcp._tools._UPDATE_STATUS_FILE", status_file)
        _write_update_status(True, ["ok", "done"])
        msg = _read_update_status()
        assert msg is not None
        assert "ok" in msg
        # File should be deleted after read
        assert not status_file.exists()

    def test_read_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("manon_mcp._tools._UPDATE_STATUS_FILE", tmp_path / "nope.json")
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
        monkeypatch.setattr("manon_mcp._hooks._config", mock_config)
        # Patch the config file path
        with patch("manon_mcp._hooks.Path") as mock_path:
            mock_path.home.return_value = tmp_path
            # Just verify it doesn't crash
            _persist_api_config()

    def test_install_hook_upgrades_legacy_post_push_path(self, tmp_path, monkeypatch):
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_file = hooks_dir / "pre-push"
        hook_file.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    "# Manon push hook - knowledge graph update + health score",
                    'python "/repo/mcp/hooks/post_push.py" "/repo"',
                    "exit 0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("manon_mcp._hooks._persist_api_config", lambda: None)

        result = _install_hook(str(tmp_path))

        assert result is not None
        updated = hook_file.read_text(encoding="utf-8").replace("\\", "/")
        assert "manon_mcp/hooks/post_push.py" in updated
        assert 'python "/repo/mcp/hooks/post_push.py" "/repo"' not in updated


class TestImpactHelpers:
    def test_find_changed_symbols_uses_line_start_line_end(self, tmp_path, monkeypatch):
        source_file = tmp_path / "sample.py"
        source_file.write_text("def foo():\n    return 1\n", encoding="utf-8")

        monkeypatch.setattr(
            "codeindex.parser.parse_file",
            lambda path: SimpleNamespace(
                error=None,
                symbols=[SimpleNamespace(name="foo", line_start=1, line_end=2)],
            ),
        )
        monkeypatch.setattr(
            "manon_mcp.tools.impact.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="@@ -1,0 +1,2 @@\n+def foo():\n+    return 1\n",
            ),
        )

        changed = _find_changed_symbols(
            changed_files=["sample.py"],
            root=tmp_path,
            git_root=tmp_path,
            prefix_with_slash="",
            base_commit="HEAD",
            commit="HEAD",
        )

        assert changed[0]["name"] == "foo"
