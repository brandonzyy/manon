"""Tests for matrixone_graph.store — CodeGraph and VectorIndex."""
import json
import pytest
from pathlib import Path

from matrixone_graph.store import Entity, Relation, Chunk, CodeGraph, VectorIndex


# ── Dataclass tests ──────────────────────────────────

class TestEntity:
    def test_create(self):
        e = Entity(id="mod.Foo", kind="class", name="Foo", file_path="mod.py", line_start=1, line_end=10)
        assert e.id == "mod.Foo"
        assert e.kind == "class"

    def test_to_dict_roundtrip(self):
        e = Entity(id="a.b", kind="function", name="b", description="desc")
        d = e.to_dict()
        e2 = Entity.from_dict(d)
        assert e == e2

    def test_from_dict_ignores_extra_keys(self):
        d = {"id": "x", "kind": "method", "name": "x", "extra": 123}
        e = Entity.from_dict(d)
        assert e.id == "x"


class TestRelation:
    def test_create(self):
        r = Relation(src_id="a", tgt_id="b", kind="calls")
        assert r.src_id == "a"
        assert r.weight == 1.0

    def test_roundtrip(self):
        r = Relation(src_id="a", tgt_id="b", kind="imports", weight=2.0)
        r2 = Relation.from_dict(r.to_dict())
        assert r == r2


class TestChunk:
    def test_roundtrip(self):
        c = Chunk(id="c1", content="def foo(): pass", file_path="f.py", line_start=1, line_end=1)
        c2 = Chunk.from_dict(c.to_dict())
        assert c == c2


# ── CodeGraph tests ──────────────────────────────────

class TestCodeGraph:
    def _make_graph(self) -> CodeGraph:
        g = CodeGraph()
        g.add_entity(Entity(id="mod.A", kind="class", name="A", file_path="mod.py"))
        g.add_entity(Entity(id="mod.B", kind="function", name="B", file_path="mod.py"))
        g.add_entity(Entity(id="util.C", kind="function", name="C", file_path="util.py"))
        g.add_relation(Relation(src_id="mod.A", tgt_id="mod.B", kind="calls"))
        g.add_relation(Relation(src_id="mod.B", tgt_id="util.C", kind="calls"))
        return g

    def test_add_get_entity(self):
        g = CodeGraph()
        e = Entity(id="x", kind="function", name="x")
        g.add_entity(e)
        got = g.get_entity("x")
        assert got is not None
        assert got.name == "x"

    def test_get_missing_entity(self):
        g = CodeGraph()
        assert g.get_entity("nope") is None

    def test_has_entity(self):
        g = CodeGraph()
        g.add_entity(Entity(id="x", kind="class", name="x"))
        assert g.has_entity("x")
        assert not g.has_entity("y")

    def test_entity_count(self):
        g = self._make_graph()
        assert g.entity_count == 3

    def test_relation_count(self):
        g = self._make_graph()
        assert g.relation_count == 2

    def test_files_indexed(self):
        g = self._make_graph()
        assert g.files_indexed() == {"mod.py", "util.py"}

    def test_remove_by_file(self):
        g = self._make_graph()
        g.remove_by_file("mod.py")
        assert not g.has_entity("mod.A")
        assert not g.has_entity("mod.B")
        assert g.has_entity("util.C")

    def test_neighbors_both(self):
        g = self._make_graph()
        results = g.neighbors("mod.B", depth=1, direction="both")
        neighbor_ids = {e.id for e, _ in results}
        assert "mod.A" in neighbor_ids
        assert "util.C" in neighbor_ids

    def test_neighbors_callers(self):
        g = self._make_graph()
        results = g.neighbors("mod.B", depth=1, direction="callers")
        neighbor_ids = {e.id for e, _ in results}
        assert "mod.A" in neighbor_ids
        assert "util.C" not in neighbor_ids

    def test_neighbors_callees(self):
        g = self._make_graph()
        results = g.neighbors("mod.B", depth=1, direction="callees")
        neighbor_ids = {e.id for e, _ in results}
        assert "util.C" in neighbor_ids
        assert "mod.A" not in neighbor_ids

    def test_neighbors_depth2(self):
        g = self._make_graph()
        results = g.neighbors("mod.A", depth=2, direction="callees")
        neighbor_ids = {e.id for e, _ in results}
        assert "mod.B" in neighbor_ids
        assert "util.C" in neighbor_ids

    def test_save_load_roundtrip(self, tmp_path):
        g = self._make_graph()
        path = tmp_path / "graph.json"
        g.save(path)
        g2 = CodeGraph()
        g2.load(path)
        assert g2.entity_count == 3
        assert g2.relation_count == 2
        e = g2.get_entity("mod.A")
        assert e is not None
        assert e.kind == "class"


# ── VectorIndex tests ────────────────────────────────

class TestVectorIndex:
    def test_add_search_entities(self):
        vi = VectorIndex()
        vi.add_entity_vectors(["a", "b"], [[1.0, 0.0], [0.0, 1.0]])
        results = vi.search_entities([1.0, 0.0], top_k=1)
        assert results[0][0] == "a"
        assert results[0][1] > 0.99

    def test_add_search_chunks(self):
        vi = VectorIndex()
        vi.add_chunk_vectors(["c1", "c2"], [[0.5, 0.5], [1.0, 0.0]])
        results = vi.search_chunks([1.0, 0.0], top_k=1)
        assert results[0][0] == "c2"

    def test_remove_by_ids(self):
        vi = VectorIndex()
        vi.add_entity_vectors(["a", "b", "c"], [[1, 0], [0, 1], [1, 1]])
        vi.remove_by_ids({"b"})
        assert vi.entity_count == 2
        results = vi.search_entities([0, 1], top_k=2)
        ids = [r[0] for r in results]
        assert "b" not in ids

    def test_empty_search(self):
        vi = VectorIndex()
        assert vi.search_entities([1, 0]) == []
        assert vi.search_chunks([1, 0]) == []

    def test_save_load_roundtrip(self, tmp_path):
        vi = VectorIndex()
        vi.add_entity_vectors(["a", "b"], [[1.0, 0.0], [0.0, 1.0]])
        vi.add_chunk_vectors(["c1"], [[0.5, 0.5]])
        path = tmp_path / "vectors.npz"
        vi.save(path)
        vi2 = VectorIndex()
        vi2.load(path)
        assert vi2.entity_count == 2
        assert vi2.chunk_count == 1
        results = vi2.search_entities([1.0, 0.0], top_k=1)
        assert results[0][0] == "a"
