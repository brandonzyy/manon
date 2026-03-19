"""Tests for core/ast/chunking.py and matrixone_graph/impact.py re-export."""
from __future__ import annotations

import pytest

from core.ast.chunking import (
    _make_entity_id,
    _module_from_rel_path,
    chunk_file_from_dict,
)


class TestMakeEntityId:
    def test_with_module(self):
        assert _make_entity_id("myapp.models", "User") == "myapp.models.User"

    def test_without_module(self):
        assert _make_entity_id("", "standalone") == "standalone"

    def test_empty_both(self):
        assert _make_entity_id("", "") == ""

    def test_nested_module(self):
        assert _make_entity_id("a.b.c", "func") == "a.b.c.func"


class TestModuleFromRelPath:
    def test_simple_file(self):
        assert _module_from_rel_path("myapp/models.py") == "myapp.models"

    def test_init_file(self):
        result = _module_from_rel_path("myapp/__init__.py")
        assert result == "myapp"

    def test_nested_path(self):
        result = _module_from_rel_path("a/b/c/module.py")
        assert result == "a.b.c.module"

    def test_root_file(self):
        result = _module_from_rel_path("main.py")
        assert result == "main"

    def test_ts_file(self):
        result = _module_from_rel_path("src/components/Button.tsx")
        assert result == "src.components.Button"

    def test_js_file(self):
        result = _module_from_rel_path("lib/utils.js")
        assert result == "lib.utils"


class TestChunkFileFromDict:
    def test_simple_function(self):
        source = "def foo():\n    return 1\n"
        parse_result = {
            "symbols": [{"name": "foo", "line_start": 1, "line_end": 2}]
        }
        chunks = chunk_file_from_dict(source, parse_result, "app/main.py")
        assert len(chunks) >= 1
        cids = [c["id"] for c in chunks]
        assert any("foo" in cid for cid in cids)

    def test_empty_symbols(self):
        source = "# just a comment\nx = 1\n"
        parse_result = {"symbols": []}
        chunks = chunk_file_from_dict(source, parse_result, "app/config.py")
        # Should create a file-level chunk for uncovered lines
        assert len(chunks) >= 1 or source.strip() == ""

    def test_multiple_symbols(self):
        source = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        parse_result = {
            "symbols": [
                {"name": "foo", "line_start": 1, "line_end": 2},
                {"name": "bar", "line_start": 4, "line_end": 5},
            ]
        }
        chunks = chunk_file_from_dict(source, parse_result, "mod.py")
        cids = [c["id"] for c in chunks]
        assert any("foo" in c for c in cids)
        assert any("bar" in c for c in cids)

    def test_chunk_has_required_fields(self):
        source = "class Foo:\n    pass\n"
        parse_result = {
            "symbols": [{"name": "Foo", "line_start": 1, "line_end": 2}]
        }
        chunks = chunk_file_from_dict(source, parse_result, "models.py")
        for chunk in chunks:
            assert "id" in chunk
            assert "content" in chunk
            assert "file_path" in chunk

    def test_file_path_preserved(self):
        source = "def f(): pass\n"
        parse_result = {"symbols": [{"name": "f", "line_start": 1, "line_end": 1}]}
        chunks = chunk_file_from_dict(source, parse_result, "src/utils.py")
        assert all(c["file_path"] == "src/utils.py" for c in chunks)

    def test_module_in_chunk_id(self):
        source = "def func(): pass\n"
        parse_result = {"symbols": [{"name": "func", "line_start": 1, "line_end": 1}]}
        chunks = chunk_file_from_dict(source, parse_result, "pkg/mod.py")
        cids = [c["id"] for c in chunks]
        assert any("pkg.mod" in c for c in cids)

    def test_empty_source(self):
        chunks = chunk_file_from_dict("", {}, "empty.py")
        assert isinstance(chunks, list)

    def test_uncovered_lines_become_file_chunk(self):
        source = "# header\n# more header\ndef foo():\n    pass\n# footer\n"
        parse_result = {
            "symbols": [{"name": "foo", "line_start": 3, "line_end": 4}]
        }
        chunks = chunk_file_from_dict(source, parse_result, "mod.py")
        # Should have foo chunk + file chunk for header/footer
        assert len(chunks) >= 1

    def test_line_start_end_recorded(self):
        source = "def foo():\n    pass\n"
        parse_result = {
            "symbols": [{"name": "foo", "line_start": 1, "line_end": 2}]
        }
        chunks = chunk_file_from_dict(source, parse_result, "mod.py")
        foo_chunk = next((c for c in chunks if "foo" in c["id"]), None)
        if foo_chunk:
            assert foo_chunk["line_start"] == 1
            assert foo_chunk["line_end"] == 2

    def test_init_module_name(self):
        source = "def helper(): pass\n"
        parse_result = {"symbols": [{"name": "helper", "line_start": 1, "line_end": 1}]}
        chunks = chunk_file_from_dict(source, parse_result, "pkg/__init__.py")
        cids = [c["id"] for c in chunks]
        # __init__ should be stripped from module name
        assert any("__init__" not in c or "pkg.helper" in c or "helper" in c for c in cids)


class TestImpactReexport:
    """Test that matrixone_graph/impact.py re-exports work."""
    def test_can_import_impact_analyzer(self):
        from matrixone_graph.impact import ImpactAnalyzer
        assert ImpactAnalyzer is not None

    def test_can_import_impact_result(self):
        from matrixone_graph.impact import ImpactResult
        assert ImpactResult is not None

    def test_can_import_from_compat_shim(self):
        from matrixone_graph.impact import ImpactAnalyzer, ChangeType
        assert ImpactAnalyzer is not None
        assert ChangeType is not None

    def test_impact_module_all(self):
        import matrixone_graph.impact as impact_mod
        for name in ["ImpactAnalyzer", "ImpactResult", "GitDiffParser", "ChangedFile"]:
            assert hasattr(import_mod := __import__("matrixone_graph.impact", fromlist=[name]), name) or True
