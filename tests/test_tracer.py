"""Tests for runtime tracer and dynamic edge merging."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from matrixone_graph.tracer import CallTracer
from matrixone_graph.merge_dynamic import (
    DYNAMIC_FILE_PATH,
    merge_dynamic_edges,
    load_dynamic_deps,
    _remove_dynamic_edges,
    _compute_weight,
)
from matrixone_graph.store import CodeGraph, Entity, Relation


# ── CallTracer tests ──────────────────────────────────


class TestCallTracer:
    def test_captures_calls(self):
        tracer = CallTracer(project_root=Path(__file__).resolve().parent.parent)
        tracer.start()
        try:
            _helper_a()
        finally:
            tracer.stop()
        edges = tracer.edges
        # Should have captured _helper_a -> _helper_b
        found = any("_helper_a" in k and "_helper_b" in k for k in edges)
        assert found, f"Expected _helper_a->_helper_b in {edges}"

    def test_save_and_load(self, tmp_path):
        tracer = CallTracer(project_root=Path(__file__).resolve().parent.parent)
        tracer.start()
        try:
            _helper_a()
        finally:
            tracer.stop()
        out = tmp_path / "deps.json"
        tracer.save(out)
        loaded = CallTracer.load(out)
        assert isinstance(loaded, dict)
        assert len(loaded) > 0

    def test_idempotent_start_stop(self):
        tracer = CallTracer()
        tracer.start()
        tracer.start()  # should not raise
        tracer.stop()
        tracer.stop()  # should not raise


def _helper_a():
    return _helper_b()


def _helper_b():
    return 42


# ── merge_dynamic tests ──────────────────────────────


class TestMergeDynamic:
    def _make_graph(self) -> CodeGraph:
        g = CodeGraph()
        g.add_entity(Entity(id="manon_mcp._tools.register", kind="function", name="register", file_path="manon_mcp/_tools.py"))
        g.add_entity(Entity(id="manon_mcp._client._get", kind="function", name="_get", file_path="manon_mcp/_client.py"))
        g.add_entity(Entity(id="core.ast_sync.scan", kind="function", name="scan", file_path="core/ast_sync.py"))
        return g

    def test_merge_adds_edges(self):
        g = self._make_graph()
        edges = {"manon_mcp._tools.register->manon_mcp._client._get": 5}
        stats = merge_dynamic_edges(g, edges)
        assert stats["added"] == 1
        assert stats["skipped"] == 0
        # Verify edge exists in graph
        assert g._g.has_edge("manon_mcp._tools.register", "manon_mcp._client._get")
        edata = g._g.edges["manon_mcp._tools.register", "manon_mcp._client._get"]
        assert edata["file_path"] == DYNAMIC_FILE_PATH
        assert "[dynamic]" in edata["description"]

    def test_skips_unknown_entities(self):
        g = self._make_graph()
        edges = {"unknown.foo->unknown.bar": 1}
        stats = merge_dynamic_edges(g, edges)
        assert stats["added"] == 0
        assert stats["skipped"] == 1

    def test_replace_removes_old_dynamic_edges(self):
        g = self._make_graph()
        # Add an old dynamic edge
        g.add_relation(Relation(
            src_id="manon_mcp._tools.register", tgt_id="core.ast_sync.scan",
            kind="calls", file_path=DYNAMIC_FILE_PATH, weight=1.0,
        ))
        assert g._g.has_edge("manon_mcp._tools.register", "core.ast_sync.scan")
        # Merge with replace=True should remove old dynamic edge
        edges = {"manon_mcp._tools.register->manon_mcp._client._get": 3}
        stats = merge_dynamic_edges(g, edges, replace=True)
        assert stats["removed"] == 1
        assert stats["added"] == 1
        assert not g._g.has_edge("manon_mcp._tools.register", "core.ast_sync.scan")

    def test_weight_computation(self):
        assert _compute_weight(1) == 1.0
        assert 1.0 < _compute_weight(2) <= 2.0
        assert _compute_weight(32) == 5.0  # 1 + log2(32) = 6 → capped at 5

    def test_load_dynamic_deps(self, tmp_path):
        data = {"a->b": 3, "c->d": 1}
        p = tmp_path / "deps.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_dynamic_deps(p)
        assert loaded == data
