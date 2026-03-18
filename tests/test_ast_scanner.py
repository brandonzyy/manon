"""Tests for core/ast/scanner.py file scanning functions."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from core.ast.scanner import _file_hash, _build_file_entry, scan_and_parse, count_scannable_files


class TestFileHash:
    def test_hash_is_string(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo(): pass\n")
        h = _file_hash(f)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA256 hex

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("def foo(): pass\n")
        f2.write_text("def foo(): pass\n")
        assert _file_hash(f1) == _file_hash(f2)

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("def foo(): pass\n")
        f2.write_text("def bar(): pass\n")
        assert _file_hash(f1) != _file_hash(f2)

    def test_empty_file_hash(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_bytes(b"")
        h = _file_hash(f)
        assert isinstance(h, str)


class TestBuildFileEntry:
    def test_valid_python_file(self, tmp_path):
        f = tmp_path / "main.py"
        f.write_text("def greet(name):\n    return f'Hello {name}'\n")
        h = _file_hash(f)
        entry = _build_file_entry(f, tmp_path, "main.py", h)
        assert entry is not None
        assert entry["rel_path"] == "main.py"
        assert entry["hash"] == h
        assert "parse_result" in entry
        assert "chunks" in entry

    def test_parse_result_has_symbols(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("def foo(): pass\nclass Bar: pass\n")
        h = _file_hash(f)
        entry = _build_file_entry(f, tmp_path, "mod.py", h)
        if entry:
            symbols = entry["parse_result"].get("symbols", [])
            names = [s.get("name") for s in symbols]
            assert "foo" in names or "Bar" in names

    def test_unsupported_file_returns_none(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        h = _file_hash(f)
        entry = _build_file_entry(f, tmp_path, "data.json", h)
        # .json is not a supported parser → returns None
        assert entry is None


class TestScanAndParse:
    def test_returns_tuple(self, tmp_path):
        (tmp_path / "app.py").write_text("def main(): pass\n")
        results, deleted, hashes = scan_and_parse(str(tmp_path), {})
        assert isinstance(results, list)
        assert isinstance(deleted, list)
        assert isinstance(hashes, dict)

    def test_finds_python_files(self, tmp_path):
        (tmp_path / "module.py").write_text("def foo(): pass\n")
        results, deleted, hashes = scan_and_parse(str(tmp_path), {})
        rel_paths = [r["rel_path"] for r in results]
        assert any("module.py" in p for p in rel_paths)

    def test_unchanged_files_skipped(self, tmp_path):
        f = tmp_path / "unchanged.py"
        f.write_text("def foo(): pass\n")
        h = _file_hash(f)
        # First scan
        results1, _, hashes1 = scan_and_parse(str(tmp_path), {})
        # Second scan with same hashes
        results2, _, hashes2 = scan_and_parse(str(tmp_path), hashes1)
        assert len(results2) == 0  # No changes

    def test_changed_file_rescanned(self, tmp_path):
        f = tmp_path / "changing.py"
        f.write_text("def v1(): pass\n")
        results1, _, hashes1 = scan_and_parse(str(tmp_path), {})
        # Modify the file
        f.write_text("def v2(): pass\n")
        results2, _, hashes2 = scan_and_parse(str(tmp_path), hashes1)
        assert any("changing.py" in r["rel_path"] for r in results2)

    def test_deleted_files_detected(self, tmp_path):
        f = tmp_path / "will_delete.py"
        f.write_text("def foo(): pass\n")
        _, _, hashes = scan_and_parse(str(tmp_path), {})
        f.unlink()
        _, deleted, _ = scan_and_parse(str(tmp_path), hashes)
        assert any("will_delete.py" in d for d in deleted)

    def test_max_files_limit(self, tmp_path):
        for i in range(5):
            (tmp_path / f"mod{i}.py").write_text(f"def f{i}(): pass\n")
        results, _, _ = scan_and_parse(str(tmp_path), {}, max_files=2)
        assert len(results) <= 2

    def test_hash_dict_populated(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        _, _, hashes = scan_and_parse(str(tmp_path), {})
        assert any("a.py" in k for k in hashes)
        assert any("b.py" in k for k in hashes)


class TestCountScannableFiles:
    def test_counts_python_files(self, tmp_path):
        (tmp_path / "a.py").write_text("# a")
        (tmp_path / "b.py").write_text("# b")
        count = count_scannable_files(str(tmp_path))
        assert count >= 2

    def test_empty_dir(self, tmp_path):
        count = count_scannable_files(str(tmp_path))
        assert count == 0

    def test_ignores_non_source_files(self, tmp_path):
        (tmp_path / "readme.md").write_text("# Readme")
        (tmp_path / "data.json").write_text("{}")
        (tmp_path / "main.py").write_text("pass")
        count = count_scannable_files(str(tmp_path))
        assert count == 1
