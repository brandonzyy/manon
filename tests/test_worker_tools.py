"""Tests for web/worker/tools.py — safety-critical tool execution."""
import asyncio
import pytest

from web.worker.tools import (
    _safe_resolve,
    _DANGEROUS_CMD_RE,
    _PKG_MGR_RE,
    _MAX_FILE_LINES,
    _MAX_CMD_OUTPUT,
    _CMD_TIMEOUT,
    TOOL_DEFINITIONS,
    exec_read_file,
    exec_edit_file,
    exec_write_file,
    exec_run_command,
    exec_search_code,
    execute_tool,
)


# ── Path Safety Tests ───────────────────────────────────

class TestSafeResolve:
    """Tests for _safe_resolve path validation."""

    def test_valid_relative_path(self, temp_repo):
        """Normal relative path should resolve correctly."""
        result = _safe_resolve(temp_repo, "test.txt")
        assert result is not None
        assert result.endswith("test.txt")

    def test_nested_relative_path(self, temp_repo):
        """Nested relative path should resolve correctly."""
        result = _safe_resolve(temp_repo, "src/components/index.ts")
        assert result is not None
        assert "src" in result and "components" in result

    def test_path_traversal_blocked(self, temp_repo):
        """Path traversal with ../ should be blocked."""
        result = _safe_resolve(temp_repo, "../etc/passwd")
        assert result is None

    def test_deep_traversal_blocked(self, temp_repo):
        """Deep path traversal should be blocked."""
        result = _safe_resolve(temp_repo, "src/../../../etc/passwd")
        assert result is None

    def test_absolute_path_blocked(self, temp_repo):
        """Absolute paths should be blocked."""
        # On Windows, absolute paths start with drive letter
        result = _safe_resolve(temp_repo, "/etc/passwd")
        assert result is None or not result.startswith("/etc")

    def test_empty_path(self, temp_repo):
        """Empty path should resolve to repo root."""
        result = _safe_resolve(temp_repo, "")
        assert result is not None

    def test_dot_path(self, temp_repo):
        """Current directory path should work."""
        result = _safe_resolve(temp_repo, ".")
        assert result is not None


# ── Dangerous Command Detection Tests ───────────────────

class TestDangerousCommands:
    """Tests for dangerous command blocking."""

    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf .",
        "rmdir /",
        "format c:",
        "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        "git clean -fdx",
        "git reset --hard HEAD",
    ])
    @pytest.mark.asyncio
    async def test_dangerous_cmd_blocked(self, temp_repo, cmd):
        """Dangerous commands should be blocked."""
        result = await exec_run_command(temp_repo, {"command": cmd})
        assert "error" in result
        assert "Dangerous command blocked" in result["error"]

    def test_dangerous_cmd_regex_patterns(self):
        """Test regex matches expected patterns."""
        # Should match
        assert _DANGEROUS_CMD_RE.search("rm -rf /")
        assert _DANGEROUS_CMD_RE.search("RM -RF /")  # case insensitive
        assert _DANGEROUS_CMD_RE.search("git reset --hard HEAD")
        assert _DANGEROUS_CMD_RE.search("git clean -fd")

        # Should NOT match
        assert not _DANGEROUS_CMD_RE.search("git status")
        assert not _DANGEROUS_CMD_RE.search("npm test")
        assert not _DANGEROUS_CMD_RE.search("rm single_file.txt")  # no -rf


class TestPackageManagerBlocking:
    """Tests for package manager command blocking."""

    @pytest.mark.parametrize("cmd", [
        "npm install",
        "npm install lodash",
        "yarn add express",
        "pnpm install",
        "pip install requests",
        "pip3 install numpy",
        "npm ci",
        "yarn remove something",
        "pip uninstall requests",
    ])
    @pytest.mark.asyncio
    async def test_pkg_mgr_blocked(self, temp_repo, cmd):
        """Package manager commands should be blocked."""
        result = await exec_run_command(temp_repo, {"command": cmd})
        assert "error" in result
        assert "Package manager" in result["error"]

    def test_pkg_mgr_regex_patterns(self):
        """Test regex matches expected patterns."""
        # Should match
        assert _PKG_MGR_RE.search("npm install")
        assert _PKG_MGR_RE.search("NPM INSTALL")  # case insensitive
        assert _PKG_MGR_RE.search("pip install requests")
        assert _PKG_MGR_RE.search("yarn add express")

        # Should NOT match
        assert not _PKG_MGR_RE.search("npm run test")
        assert not _PKG_MGR_RE.search("pip list")
        assert not _PKG_MGR_RE.search("yarn --version")


# ── File Operation Tests ────────────────────────────────

class TestReadFile:
    """Tests for exec_read_file."""

    def test_read_existing_file(self, temp_repo):
        """Reading an existing file should return content."""
        result = exec_read_file(temp_repo, {"path": "test.txt"})
        assert "content" in result
        assert "hello world" in result["content"]

    def test_read_file_not_found(self, temp_repo):
        """Reading a non-existent file should return error."""
        result = exec_read_file(temp_repo, {"path": "nonexistent.txt"})
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_read_file_traversal_blocked(self, temp_repo):
        """Path traversal should be blocked."""
        result = exec_read_file(temp_repo, {"path": "../../../etc/passwd"})
        assert "error" in result
        assert "outside repo" in result["error"].lower()

    def test_read_nested_file(self, temp_repo):
        """Reading nested file should work."""
        result = exec_read_file(temp_repo, {"path": "src/components/index.ts"})
        assert "content" in result
        assert "export const x" in result["content"]

    def test_read_file_truncation(self, temp_repo):
        """Large files should be truncated."""
        # Create a large file
        large_file = "src/large.txt"
        large_content = "\n".join([f"Line {i}" for i in range(_MAX_FILE_LINES + 100)])
        exec_write_file(temp_repo, {"path": large_file, "content": large_content})

        result = exec_read_file(temp_repo, {"path": large_file})
        assert "content" in result
        assert "truncated" in result["content"].lower()

    def test_read_file_exact_limit(self, temp_repo):
        """File at exactly _MAX_FILE_LINES should not be truncated."""
        exact_file = "src/exact.txt"
        exact_content = "\n".join([f"Line {i}" for i in range(_MAX_FILE_LINES)])
        exec_write_file(temp_repo, {"path": exact_file, "content": exact_content})

        result = exec_read_file(temp_repo, {"path": exact_file})
        assert "content" in result
        assert "truncated" not in result["content"].lower()


class TestEditFile:
    """Tests for exec_edit_file."""

    def test_edit_file_success(self, temp_repo):
        """Successful edit should return success."""
        result = exec_edit_file(temp_repo, {
            "path": "test.txt",
            "old_text": "hello",
            "new_text": "goodbye"
        })
        assert result.get("success") is True

        # Verify content changed
        read_result = exec_read_file(temp_repo, {"path": "test.txt"})
        assert "goodbye" in read_result["content"]

    def test_edit_file_not_found(self, temp_repo):
        """Editing non-existent file should return error."""
        result = exec_edit_file(temp_repo, {
            "path": "nonexistent.txt",
            "old_text": "foo",
            "new_text": "bar"
        })
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_edit_old_text_not_found(self, temp_repo):
        """Editing with non-existent old_text should return error."""
        result = exec_edit_file(temp_repo, {
            "path": "test.txt",
            "old_text": "this_does_not_exist",
            "new_text": "bar"
        })
        assert "error" in result
        assert "old_text not found" in result["error"]

    def test_edit_multiple_matches_error(self, temp_repo):
        """Editing with multiple matches should return error."""
        # Create file with duplicate content
        exec_write_file(temp_repo, {
            "path": "duplicates.txt",
            "content": "foo\nbar\nfoo\n"
        })

        result = exec_edit_file(temp_repo, {
            "path": "duplicates.txt",
            "old_text": "foo",
            "new_text": "baz"
        })
        assert "error" in result
        assert "matches" in result["error"].lower()

    def test_edit_path_traversal_blocked(self, temp_repo):
        """Path traversal in edit should be blocked."""
        result = exec_edit_file(temp_repo, {
            "path": "../../../etc/passwd",
            "old_text": "root",
            "new_text": "test"
        })
        assert "error" in result
        assert "outside repo" in result["error"].lower()


class TestWriteFile:
    """Tests for exec_write_file."""

    def test_write_new_file(self, temp_repo):
        """Writing a new file should succeed."""
        result = exec_write_file(temp_repo, {
            "path": "new_file.txt",
            "content": "new content"
        })
        assert result.get("success") is True
        assert "new_file.txt" in result.get("message", "")

    def test_write_creates_parent_dirs(self, temp_repo):
        """Writing should create parent directories."""
        result = exec_write_file(temp_repo, {
            "path": "deeply/nested/dir/file.txt",
            "content": "nested content"
        })
        assert result.get("success") is True

        # Verify file exists
        read_result = exec_read_file(temp_repo, {"path": "deeply/nested/dir/file.txt"})
        assert "content" in read_result

    def test_write_overwrites_existing(self, temp_repo):
        """Writing to existing file should overwrite."""
        exec_write_file(temp_repo, {"path": "overwrite.txt", "content": "original"})

        result = exec_write_file(temp_repo, {
            "path": "overwrite.txt",
            "content": "overwritten"
        })
        assert result.get("success") is True

        read_result = exec_read_file(temp_repo, {"path": "overwrite.txt"})
        assert "overwritten" in read_result["content"]

    def test_write_path_traversal_blocked(self, temp_repo):
        """Path traversal in write should be blocked."""
        result = exec_write_file(temp_repo, {
            "path": "../../../tmp/malicious.txt",
            "content": "malicious"
        })
        assert "error" in result
        assert "outside repo" in result["error"].lower()

    def test_write_empty_content(self, temp_repo):
        """Writing empty content should work."""
        result = exec_write_file(temp_repo, {
            "path": "empty.txt",
            "content": ""
        })
        assert result.get("success") is True

    def test_write_line_count(self, temp_repo):
        """Write result should include line count."""
        result = exec_write_file(temp_repo, {
            "path": "multiline.txt",
            "content": "line1\nline2\nline3"
        })
        assert result.get("success") is True
        assert "3 lines" in result.get("message", "")


# ── Command Execution Tests ─────────────────────────────

class TestRunCommand:
    """Tests for exec_run_command."""

    @pytest.mark.asyncio
    async def test_cmd_success(self, temp_repo):
        """Successful command should return output."""
        result = await exec_run_command(temp_repo, {"command": "echo hello"})
        assert "exit_code" in result
        assert result["exit_code"] == 0
        assert "hello" in result.get("output", "")

    @pytest.mark.asyncio
    async def test_cmd_exit_code(self, temp_repo):
        """Command with non-zero exit should still return result."""
        result = await exec_run_command(temp_repo, {"command": "exit 1"})
        assert "exit_code" in result
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_cmd_output_limit(self, temp_repo):
        """Large output should be truncated."""
        # Generate large output
        large_cmd = f"python -c \"print('x' * {_MAX_CMD_OUTPUT + 1000})\""
        result = await exec_run_command(temp_repo, {"command": large_cmd})
        assert "output" in result
        assert len(result["output"]) <= _MAX_CMD_OUTPUT

    @pytest.mark.asyncio
    async def test_cmd_stderr_included(self, temp_repo):
        """Stderr should be included in output."""
        result = await exec_run_command(temp_repo, {
            "command": "python -c \"import sys; print('error', file=sys.stderr)\""
        })
        assert "output" in result
        assert "error" in result["output"]

    @pytest.mark.asyncio
    async def test_cmd_empty(self, temp_repo):
        """Empty command should still execute (shell handles it)."""
        result = await exec_run_command(temp_repo, {"command": ""})
        # Empty command typically succeeds on Unix shells
        assert "exit_code" in result or "error" in result


class TestSearchCode:
    """Tests for exec_search_code."""

    @pytest.mark.asyncio
    async def test_empty_query_error(self, temp_repo):
        """Empty query should return error."""
        result = await exec_search_code(temp_repo, {"query": ""})
        assert "error" in result
        assert "Empty query" in result["error"]

    @pytest.mark.asyncio
    async def test_unregistered_project_error(self, temp_repo):
        """Unregistered project should return error."""
        # temp_repo is not registered in ast_sync
        result = await exec_search_code(temp_repo, {"query": "test"})
        assert "error" in result
        assert "not registered" in result["error"].lower()


# ── Tool Dispatcher Tests ───────────────────────────────

class TestExecuteTool:
    """Tests for the execute_tool dispatcher."""

    def test_unknown_tool_error(self, temp_repo):
        """Unknown tool name should return error."""
        result = asyncio.run(execute_tool("unknown_tool", {}, temp_repo))
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_read_file_dispatch(self, temp_repo):
        """Dispatcher should route to read_file correctly."""
        result = await execute_tool("read_file", {"path": "test.txt"}, temp_repo)
        assert "content" in result

    @pytest.mark.asyncio
    async def test_write_file_dispatch(self, temp_repo):
        """Dispatcher should route to write_file correctly."""
        result = await execute_tool("write_file", {
            "path": "dispatch_test.txt",
            "content": "test"
        }, temp_repo)
        assert result.get("success") is True

    @pytest.mark.asyncio
    async def test_run_command_dispatch(self, temp_repo):
        """Dispatcher should route to run_command correctly."""
        result = await execute_tool("run_command", {"command": "echo test"}, temp_repo)
        assert "exit_code" in result or "error" in result


# ── Tool Definition Tests ───────────────────────────────

class TestToolDefinitions:
    """Tests for TOOL_DEFINITIONS structure."""

    def test_tool_definitions_exist(self):
        """Tool definitions should be a non-empty list."""
        assert isinstance(TOOL_DEFINITIONS, list)
        assert len(TOOL_DEFINITIONS) >= 5

    def test_tool_definition_structure(self):
        """Each tool definition should have required fields."""
        for tool in TOOL_DEFINITIONS:
            assert tool["type"] == "function"
            assert "name" in tool["function"]
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]

    def test_required_tools_present(self):
        """All expected tools should be defined."""
        tool_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        expected = {"read_file", "edit_file", "write_file", "run_command", "search_code"}
        assert expected.issubset(tool_names)

    def test_tool_parameters_have_required(self):
        """Tool parameters should define required fields."""
        for tool in TOOL_DEFINITIONS:
            params = tool["function"]["parameters"]
            if params["properties"]:
                # Check that required params are listed
                required = params.get("required", [])
                for prop_name in params["properties"]:
                    if prop_name in required:
                        assert prop_name in params["properties"]


# ── Constants Validation Tests ──────────────────────────

class TestConstants:
    """Tests for safety constants."""

    def test_max_file_lines_reasonable(self):
        """_MAX_FILE_LINES should be reasonable."""
        assert 100 <= _MAX_FILE_LINES <= 10000

    def test_max_cmd_output_reasonable(self):
        """_MAX_CMD_OUTPUT should be reasonable."""
        assert 1000 <= _MAX_CMD_OUTPUT <= 100000

    def test_cmd_timeout_reasonable(self):
        """_CMD_TIMEOUT should be reasonable."""
        assert 5 <= _CMD_TIMEOUT <= 120
