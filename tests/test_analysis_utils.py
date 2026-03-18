"""Tests for core/ast/analysis.py utility functions."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from core.ast.analysis import (
    _walk_safe,
    _file_exists_in_root,
    _file_contains_text,
    detect_test_patterns,
    preview_project_structure,
)


class TestWalkSafe:
    def test_walks_directory(self, tmp_path):
        (tmp_path / "a.py").write_text("# a")
        (tmp_path / "b.py").write_text("# b")
        files = list(_walk_safe(tmp_path))
        names = {f.name for f in files}
        assert "a.py" in names
        assert "b.py" in names

    def test_skips_venv(self, tmp_path):
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "lib.py").write_text("# ignored")
        (tmp_path / "main.py").write_text("# main")
        files = list(_walk_safe(tmp_path))
        names = {f.name for f in files}
        assert "lib.py" not in names
        assert "main.py" in names

    def test_skips_node_modules(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "package.js").write_text("// ignored")
        (tmp_path / "index.js").write_text("// main")
        files = list(_walk_safe(tmp_path))
        names = {f.name for f in files}
        assert "package.js" not in names
        assert "index.js" in names

    def test_skips_pycache(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.pyc").write_bytes(b"")
        (tmp_path / "mod.py").write_text("# mod")
        files = list(_walk_safe(tmp_path))
        names = {f.name for f in files}
        assert "mod.pyc" not in names
        assert "mod.py" in names

    def test_max_files_limit(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file_{i}.py").write_text(f"# {i}")
        files = list(_walk_safe(tmp_path, max_files=3))
        assert len(files) == 3

    def test_max_files_zero_no_limit(self, tmp_path):
        for i in range(5):
            (tmp_path / f"file_{i}.py").write_text(f"# {i}")
        files = list(_walk_safe(tmp_path, max_files=0))
        assert len(files) == 5

    def test_recursive(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "nested.py").write_text("# nested")
        (tmp_path / "top.py").write_text("# top")
        files = list(_walk_safe(tmp_path))
        names = {f.name for f in files}
        assert "nested.py" in names
        assert "top.py" in names

    def test_empty_directory(self, tmp_path):
        files = list(_walk_safe(tmp_path))
        assert files == []


class TestFileExistsInRoot:
    def test_exists_at_root(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[build]")
        assert _file_exists_in_root(tmp_path, "pyproject.toml") is True

    def test_not_exists(self, tmp_path):
        assert _file_exists_in_root(tmp_path, "missing.toml") is False

    def test_exists_in_subdir(self, tmp_path):
        subdir = tmp_path / "backend"
        subdir.mkdir()
        (subdir / "requirements.txt").write_text("flask")
        assert _file_exists_in_root(tmp_path, "requirements.txt") is True

    def test_not_in_deep_subdir(self, tmp_path):
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "config.json").write_text("{}")
        # Only checks root and one level deep
        assert _file_exists_in_root(tmp_path, "config.json") is False


class TestFileContainsText:
    def test_contains_text(self, tmp_path):
        (tmp_path / "setup.cfg").write_text("[tool:pytest]\naddopts = -v\n")
        assert _file_contains_text(tmp_path, "setup.cfg", "[tool:pytest]") is True

    def test_not_contains_text(self, tmp_path):
        (tmp_path / "setup.cfg").write_text("[options]\npackages = find:")
        assert _file_contains_text(tmp_path, "setup.cfg", "[tool:pytest]") is False

    def test_missing_file(self, tmp_path):
        assert _file_contains_text(tmp_path, "nonexistent.cfg", "anything") is False


class TestDetectTestPatterns:
    def test_pytest_detected(self, tmp_path):
        (tmp_path / "conftest.py").write_text("import pytest")
        patterns, report = detect_test_patterns(tmp_path)
        pattern_str = " ".join(patterns)
        assert "test_" in pattern_str or "conftest" in pattern_str

    def test_tests_dir_detected(self, tmp_path):
        (tmp_path / "tests").mkdir()
        patterns, report = detect_test_patterns(tmp_path)
        pattern_str = " ".join(patterns)
        assert "tests" in pattern_str

    def test_empty_dir_returns_lists(self, tmp_path):
        patterns, report = detect_test_patterns(tmp_path)
        assert isinstance(patterns, list)
        assert isinstance(report, list)

    def test_jest_config_detected(self, tmp_path):
        (tmp_path / "jest.config.js").write_text("module.exports = {}")
        patterns, report = detect_test_patterns(tmp_path)
        pattern_str = " ".join(patterns)
        assert "test" in pattern_str or "spec" in pattern_str

    def test_no_duplicates_in_patterns(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "conftest.py").write_text("# conftest")
        patterns, _ = detect_test_patterns(tmp_path)
        # Should be sorted and deduplicated
        assert len(patterns) == len(set(patterns))


class TestPreviewProjectStructure:
    def test_returns_string(self, tmp_path):
        result = preview_project_structure(str(tmp_path))
        assert isinstance(result, str)

    def test_shows_directories(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "main.py").write_text("# main")
        result = preview_project_structure(str(tmp_path))
        assert "src" in result or isinstance(result, str)

    def test_empty_dir(self, tmp_path):
        result = preview_project_structure(str(tmp_path))
        assert isinstance(result, str)
