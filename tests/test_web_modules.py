"""Tests for web/ modules — worker tools, pool, coach pipeline, ws_hub."""
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from web.worker.tools import (
    _safe_resolve, exec_read_file, exec_edit_file, exec_write_file,
    TOOL_DEFINITIONS, _DANGEROUS_CMD_RE, _PKG_MGR_RE,
    _MAX_FILE_LINES, _MAX_CMD_OUTPUT, _CMD_TIMEOUT,
)
from web.worker.pool import WorkerPool, MAX_WORKERS
from web.coach.pipeline import Status, FeatureState, get_session, _ensure_session, _sessions
from web.ws_hub import WSHub


# ── Worker Tools ─────────────────────────────────────

class TestSafeResolve:
    def test_valid_path(self, tmp_path):
        (tmp_path / "foo.py").touch()
        result = _safe_resolve(str(tmp_path), "foo.py")
        assert result is not None
        assert "foo.py" in result

    def test_traversal_blocked(self, tmp_path):
        result = _safe_resolve(str(tmp_path), "../../etc/passwd")
        assert result is None

    def test_nested_path(self, tmp_path):
        d = tmp_path / "sub"
        d.mkdir()
        (d / "bar.py").touch()
        result = _safe_resolve(str(tmp_path), "sub/bar.py")
        assert result is not None


class TestExecReadFile:
    def test_read_existing(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("print('hello')", encoding="utf-8")
        result = exec_read_file(str(tmp_path), {"path": "hello.py"})
        assert "content" in result
        assert "hello" in result["content"]

    def test_read_missing(self, tmp_path):
        result = exec_read_file(str(tmp_path), {"path": "nope.py"})
        assert "error" in result

    def test_read_traversal(self, tmp_path):
        result = exec_read_file(str(tmp_path), {"path": "../../etc/passwd"})
        assert "error" in result

    def test_read_truncation(self, tmp_path):
        f = tmp_path / "big.py"
        f.write_text("\n".join(f"line {i}" for i in range(1000)), encoding="utf-8")
        result = exec_read_file(str(tmp_path), {"path": "big.py"})
        assert "truncated" in result["content"]


class TestExecEditFile:
    def test_edit_success(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\ny = 2\n", encoding="utf-8")
        result = exec_edit_file(str(tmp_path), {"path": "code.py", "old_text": "x = 1", "new_text": "x = 42"})
        assert result.get("success") is True
        assert "42" in f.read_text(encoding="utf-8")

    def test_edit_not_found(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\n", encoding="utf-8")
        result = exec_edit_file(str(tmp_path), {"path": "code.py", "old_text": "y = 2", "new_text": "y = 3"})
        assert "error" in result

    def test_edit_ambiguous(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\nx = 1\n", encoding="utf-8")
        result = exec_edit_file(str(tmp_path), {"path": "code.py", "old_text": "x = 1", "new_text": "x = 2"})
        assert "error" in result
        assert "matches" in result["error"]

    def test_edit_missing_file(self, tmp_path):
        result = exec_edit_file(str(tmp_path), {"path": "nope.py", "old_text": "a", "new_text": "b"})
        assert "error" in result


class TestExecWriteFile:
    def test_write_new(self, tmp_path):
        result = exec_write_file(str(tmp_path), {"path": "new.py", "content": "x = 1\n"})
        assert result.get("success") is True
        assert (tmp_path / "new.py").read_text(encoding="utf-8") == "x = 1\n"

    def test_write_nested(self, tmp_path):
        result = exec_write_file(str(tmp_path), {"path": "sub/dir/file.py", "content": "y = 2"})
        assert result.get("success") is True

    def test_write_traversal(self, tmp_path):
        result = exec_write_file(str(tmp_path), {"path": "../../evil.py", "content": "bad"})
        assert "error" in result


class TestToolDefinitions:
    def test_has_tools(self):
        assert len(TOOL_DEFINITIONS) >= 4

    def test_tool_names(self):
        names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
        assert "read_file" in names
        assert "edit_file" in names
        assert "write_file" in names
        assert "run_command" in names


class TestSafetyRegex:
    def test_dangerous_cmd(self):
        assert _DANGEROUS_CMD_RE.search("rm -rf /")
        assert _DANGEROUS_CMD_RE.search("git reset --hard")
        assert not _DANGEROUS_CMD_RE.search("ls -la")

    def test_pkg_mgr(self):
        assert _PKG_MGR_RE.search("npm install foo")
        assert _PKG_MGR_RE.search("pip install bar")
        assert not _PKG_MGR_RE.search("python test.py")


class TestConstants:
    def test_limits(self):
        assert _MAX_FILE_LINES > 0
        assert _MAX_CMD_OUTPUT > 0
        assert _CMD_TIMEOUT > 0


# ── WorkerPool ───────────────────────────────────────

class TestWorkerPool:
    def test_init(self):
        pool = WorkerPool(max_workers=3)
        assert pool._max == 3
        assert pool.active_count == 0

    def test_default_max(self):
        pool = WorkerPool()
        assert pool._max == MAX_WORKERS

    def test_max_workers_constant(self):
        assert MAX_WORKERS == 5


# ── Coach Pipeline ───────────────────────────────────

class TestStatus:
    def test_all_values(self):
        expected = {"idle", "clarifying", "spec-ready", "user-confirming",
                    "designing", "decomposing", "executing", "reviewing", "done", "failed"}
        actual = {s.value for s in Status}
        assert actual == expected

    def test_string_enum(self):
        assert Status.IDLE == "idle"
        assert isinstance(Status.IDLE, str)


class TestFeatureState:
    def test_defaults(self):
        fs = FeatureState()
        assert fs.feature_id == ""
        assert fs.status == Status.IDLE
        assert fs.conversation_history == []
        assert fs.spec is None
        assert fs.tasks == []
        assert fs.current_task_idx == -1
        assert fs.failed_attempts == 0

    def test_with_values(self):
        fs = FeatureState(dev_id="dev-1", status=Status.EXECUTING, description="test feature")
        assert fs.dev_id == "dev-1"
        assert fs.status == Status.EXECUTING


class TestSessionManagement:
    def test_get_session_missing(self):
        _sessions.clear()
        assert get_session("nonexistent") is None

    def test_ensure_session_creates(self):
        _sessions.clear()
        fs = _ensure_session("dev-1")
        assert fs.dev_id == "dev-1"
        assert "dev-1" in _sessions
        _sessions.clear()

    def test_ensure_session_returns_existing(self):
        _sessions.clear()
        fs1 = _ensure_session("dev-1")
        fs1.status = Status.EXECUTING
        fs2 = _ensure_session("dev-1")
        assert fs2.status == Status.EXECUTING
        _sessions.clear()


# ── WSHub ────────────────────────────────────────────

class TestWSHub:
    def test_init(self):
        hub = WSHub()
        assert hub._devs == {}
        assert hub._agents == {}
        assert hub._monitors == []

    def test_next_dev_id(self):
        hub = WSHub()
        id1 = hub._next_dev_id()
        id2 = hub._next_dev_id()
        assert id1 == "dev-1"
        assert id2 == "dev-2"

    def test_remove_dev(self):
        hub = WSHub()
        hub._devs["dev-1"] = MagicMock()
        hub.remove_dev("dev-1")
        assert "dev-1" not in hub._devs

    def test_remove_dev_missing(self):
        hub = WSHub()
        hub.remove_dev("nonexistent")  # should not raise
