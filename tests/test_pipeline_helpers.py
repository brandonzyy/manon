"""Tests for matrixone_graph pipeline helper functions."""
import pytest
from pathlib import Path

from matrixone_graph.pipeline import (
    _resolve_import_by_filepath,
    _build_description, _resolve_callee, _format_query_context,
    QueryResult,
    _load_meta, _save_meta, _load_chunks, _save_chunks,
    GRAPH_FILE, VECTORS_FILE, CHUNKS_FILE, META_FILE, KG_DIR,
)
from core.ast.chunking import _module_from_rel_path, _make_entity_id

def _module_prefix(file_path, base_path):
    """Adapter: compute module prefix from absolute path + base dir."""
    rel = str(file_path.relative_to(base_path)).replace("\\", "/")
    return _module_from_rel_path(rel)
from matrixone_graph.store import Chunk


class TestModulePrefix:
    def test_simple(self, tmp_path):
        f = tmp_path / "foo.py"
        f.touch()
        assert _module_prefix(f, tmp_path) == "foo"

    def test_nested(self, tmp_path):
        d = tmp_path / "pkg" / "sub"
        d.mkdir(parents=True)
        f = d / "mod.py"
        f.touch()
        assert _module_prefix(f, tmp_path) == "pkg.sub.mod"

    def test_init(self, tmp_path):
        d = tmp_path / "pkg"
        d.mkdir()
        f = d / "__init__.py"
        f.touch()
        assert _module_prefix(f, tmp_path) == "pkg"


class TestMakeEntityId:
    def test_with_module(self):
        assert _make_entity_id("foo.bar", "Baz") == "foo.bar.Baz"

    def test_empty_module(self):
        assert _make_entity_id("", "Baz") == "Baz"


class TestResolveImport:
    def test_absolute(self):
        assert _resolve_import_by_filepath("a/b.py", "os.path") == "os.path"

    def test_relative(self):
        result = _resolve_import_by_filepath("pkg/sub/mod.py", "../util")
        assert result == "pkg.util"

    def test_dot_relative(self):
        result = _resolve_import_by_filepath("pkg/mod.py", "./helper")
        assert result == "pkg.helper"


class TestResolveCallee:
    def test_local(self):
        result = _resolve_callee("foo", {"foo"}, {}, "mod", "mod.py")
        assert result == "mod.foo"

    def test_imported(self):
        result = _resolve_callee("bar", set(), {"bar": "ext.bar"}, "mod", "mod.py")
        assert result == "ext.bar"

    def test_dotted_imported_prefix(self):
        result = _resolve_callee("client.get", set(), {"client": "http.client"}, "mod", "mod.py")
        assert result == "http.client.get"

    def test_unknown(self):
        result = _resolve_callee("unknown", set(), {}, "mod", "mod.py")
        assert result == "unknown"


class TestFormatQueryContext:
    def test_empty(self):
        assert _format_query_context([], [], []) == ""

    def test_with_entities(self):
        entities = [{"kind": "function", "name": "foo", "file_path": "a.py",
                     "line_start": 1, "score": 0.9, "description": "a func"}]
        ctx = _format_query_context(entities, [], [])
        assert "foo" in ctx
        assert "Matched Entities" in ctx

    def test_with_relations(self):
        rels = [{"src_id": "a", "tgt_id": "b", "kind": "calls"}]
        ctx = _format_query_context([], rels, [])
        assert "Relations" in ctx

    def test_with_chunks(self):
        chunks = [{"file_path": "a.py", "line_start": 1, "line_end": 5,
                   "symbol_name": "foo", "score": 0.8, "content": "def foo(): pass"}]
        ctx = _format_query_context([], [], chunks)
        assert "Code Snippets" in ctx


class TestMetaAndChunks:
    def test_meta_roundtrip(self, tmp_path):
        meta = {"version": 1, "hashes": {"a.py": "abc"}}
        _save_meta(tmp_path, meta)
        loaded = _load_meta(tmp_path)
        assert loaded["hashes"]["a.py"] == "abc"

    def test_meta_missing(self, tmp_path):
        meta = _load_meta(tmp_path / "nope")
        assert meta["version"] == 1

    def test_chunks_roundtrip(self, tmp_path):
        chunks = {"c1": Chunk(id="c1", content="hello", file_path="a.py")}
        _save_chunks(tmp_path, chunks)
        loaded = _load_chunks(tmp_path)
        assert "c1" in loaded
        assert loaded["c1"].content == "hello"

    def test_chunks_missing(self, tmp_path):
        assert _load_chunks(tmp_path / "nope") == {}


class TestDataclasses:
    def test_query_result(self):
        r = QueryResult()
        assert r.entities == []
        assert r.context == ""
