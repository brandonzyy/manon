"""Tests for mcp helper functions — server.py, _config.py, _hooks.py."""
import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from manon_mcp._hooks import (
    _MANON_SCOPE,
    _PRE_AGENT_PLAN_HOOK,
    _PRE_SEARCH_HOOK,
    _POST_COMMIT_HOOK,
    _install_hook,
    _persist_api_config,
)
from manon_mcp.query_state import STATE_FILE, record_query
from manon_mcp.server import _write_update_status, _read_update_status, _UPDATE_STATUS_FILE
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
        monkeypatch.setattr("manon_mcp.server._UPDATE_STATUS_FILE", status_file)
        _write_update_status(True, ["ok", "done"])
        msg = _read_update_status()
        assert msg is not None
        assert "ok" in msg
        # File should be deleted after read
        assert not status_file.exists()

    def test_read_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("manon_mcp.server._UPDATE_STATUS_FILE", tmp_path / "nope.json")
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

    def test_post_commit_hook_is_valid_python(self):
        compile(_POST_COMMIT_HOOK, "<post_commit>", "exec")

    def test_pre_agent_plan_hook_is_valid_python(self):
        compile(_PRE_AGENT_PLAN_HOOK, "<pre_agent_plan>", "exec")

    def test_manon_scope_is_valid_python(self):
        compile(_MANON_SCOPE, "<manon_scope>", "exec")


def _scope_functions():
    """exec 生成器里的 _MANON_SCOPE 字符串，拿到可调用的钩子侧函数。"""
    ns = {}
    exec(compile(_MANON_SCOPE, "<manon_scope>", "exec"), ns)
    return ns


class TestQueryState:
    """record_query（MCP 服务端写）与 manon_queried（钩子读）只共享
    last_query.json 一个文件格式——这里用同一个 tmp home 做 round-trip，
    钉住格式不漂移；漂移的表现是钩子在刚查过的仓里继续拦。"""

    def test_roundtrip_fresh_query_allows(self, tmp_path, monkeypatch):
        monkeypatch.setattr("manon_mcp.query_state.STATE_FILE", tmp_path / ".manon" / "last_query.json")
        monkeypatch.setattr(
            "manon_mcp.query_state.find_project_by_repo_id",
            lambda rid: (str(tmp_path / "repo"), {"repo_id": rid}),
        )
        record_query("abc12345")
        assert _scope_functions()["manon_queried"](tmp_path / "repo", home=tmp_path) is True

    def test_stale_or_absent_entry_blocks(self, tmp_path):
        state_file = tmp_path / ".manon" / "last_query.json"
        state_file.parent.mkdir()
        state_file.write_text(json.dumps({str(tmp_path / "repo"): 1000.0}), encoding="utf-8")
        scope = _scope_functions()
        just_inside = scope["manon_queried"](tmp_path / "repo", home=tmp_path, now=1000.0 + 3600)
        just_outside = scope["manon_queried"](tmp_path / "repo", home=tmp_path, now=1000.0 + 3600 + 1)
        assert just_inside is True
        assert just_outside is False
        # 文件在、结构好，但没有这个仓的条目——明确没查过，不是「不可知」
        state_file.write_text(json.dumps({"other": 1.0}), encoding="utf-8")
        assert scope["manon_queried"](tmp_path / "repo", home=tmp_path) is False
        # 条目在但不是数——同上，按没查过处理
        state_file.write_text(json.dumps({str(tmp_path / "repo"): "soon"}), encoding="utf-8")
        assert scope["manon_queried"](tmp_path / "repo", home=tmp_path) is False

    def test_missing_or_corrupt_state_fail_open(self, tmp_path):
        scope = _scope_functions()
        # 状态文件不存在（冷启动：还没装过会写它的服务端版本）
        assert scope["manon_queried"](tmp_path / "repo", home=tmp_path) is True
        # 状态文件读得了但不是 JSON
        state_file = tmp_path / ".manon" / "last_query.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("not json", encoding="utf-8")
        assert scope["manon_queried"](tmp_path / "repo", home=tmp_path) is True
        # 合法 JSON 但不是预期的结构
        state_file.write_text(json.dumps([1, 2]), encoding="utf-8")
        assert scope["manon_queried"](tmp_path / "repo", home=tmp_path) is True

    def test_unregistered_repo_writes_nothing(self, tmp_path, monkeypatch):
        state_file = tmp_path / "last_query.json"
        monkeypatch.setattr("manon_mcp.query_state.STATE_FILE", state_file)
        monkeypatch.setattr(
            "manon_mcp.query_state.find_project_by_repo_id", lambda _rid: None)
        record_query("nobody")
        assert not state_file.exists()

    def test_state_file_lives_under_manon_dir(self):
        # 读取侧（钩子）按 <home>/.manon/last_query.json 拼，写入方必须落同一个位置
        scope = _scope_functions()
        assert scope["QUERY_STATE"] == ".manon/last_query.json"
        assert STATE_FILE == Path.home() / ".manon" / "last_query.json"


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
        assert "nohup" in updated
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
