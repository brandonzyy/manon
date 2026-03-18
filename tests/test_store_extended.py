"""Extended tests for matrixone_graph/store.py — edge cases, error handling, boundaries."""
import json
import pytest
import numpy as np
from pathlib import Path

from matrixone_graph.store import Entity, Relation, Chunk, CodeGraph, VectorIndex


class TestEntityEdgeCases:
    def test_empty_defaults(self):
        e = Entity(id="x", kind="function", name="x")
        assert e.description == ""
        assert e.file_path == ""
        assert e.line_start == 0
        assert e.decorators == []

    def test_from_dict_missing_fields(self):
        e = Entity.from_dict({"id": "x", "kind": "class", "name": "X"})
        assert e.line_end == 0

    def test_with_decorators(self):
        e = Entity(id="x", kind="function", name="x", decorators=["app.route", "login_required"])
        d = e.to_dict()
        assert len(d["decorators"]) == 2


class TestRelationEdgeCases:
    def test_default_weight(self):
        r = Relation(src_id="a", tgt_id="b", kind="calls")
        assert r.weight == 1.0

    def test_custom_weight(self):
        r = Relation(src_id="a", tgt_id="b", kind="calls", weight=3.5)
        assert r.weight == 3.5

    def test_with_file_path(self):
        r = Relation(src_id="a", tgt_id="b", kind="imports", file_path="mod.py")
        assert r.file_path == "mod.py"


class TestChunkEdgeCases:
    def test_empty_content(self):
        c = Chunk(id="c1", content="")
        assert c.file_path == ""
        assert c.symbol_name == ""

    def test_with_all_fields(self):
        c = Chunk(id="c1", content="code", file_path="a.py", line_start=1, line_end=10, symbol_name="foo")
        d = c.to_dict()
        assert d["symbol_name"] == "foo"


class TestCodeGraphEdgeCases:
    def test_empty_graph(self):
        g = CodeGraph()
        assert g.entity_count == 0
        assert g.relation_count == 0
        assert g.files_indexed() == set()

    def test_get_phantom_node(self):
        """Phantom nodes (auto-created by edges) should return None."""
        g = CodeGraph()
        g.add_relation(Relation(src_id="a", tgt_id="b", kind="calls"))
        # a and b exist as nodes but have no 'kind' data
        assert g.get_entity("a") is None
        assert g.has_entity("a")  # node exists

    def test_neighbors_missing_entity(self):
        g = CodeGraph()
        assert g.neighbors("nonexistent") == []

    def test_remove_by_file_empty(self):
        g = CodeGraph()
        g.remove_by_file("nonexistent.py")  # should not raise

    def test_save_load_empty(self, tmp_path):
        g = CodeGraph()
        path = tmp_path / "empty.json"
        g.save(path)
        g2 = CodeGraph()
        g2.load(path)
        assert g2.entity_count == 0

    def test_load_nonexistent(self, tmp_path):
        g = CodeGraph()
        g.load(tmp_path / "nope.json")
        assert g.entity_count == 0

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        g = CodeGraph()
        g.load(path)  # should not raise
        assert g.entity_count == 0

    def test_neighbors_with_relations(self):
        g = CodeGraph()
        g.add_entity(Entity(id="a", kind="function", name="a"))
        g.add_entity(Entity(id="b", kind="function", name="b"))
        g.add_relation(Relation(src_id="a", tgt_id="b", kind="calls"))
        results = g.neighbors("a", depth=1, direction="callees")
        assert len(results) == 1
        ent, rels = results[0]
        assert ent.id == "b"
        assert len(rels) >= 1
        assert rels[0].kind == "calls"

    def test_multiple_relations_same_pair(self):
        g = CodeGraph()
        g.add_entity(Entity(id="a", kind="function", name="a"))
        g.add_entity(Entity(id="b", kind="function", name="b"))
        g.add_relation(Relation(src_id="a", tgt_id="b", kind="calls"))
        # Adding another edge overwrites in DiGraph
        g.add_relation(Relation(src_id="a", tgt_id="b", kind="imports"))
        assert g.relation_count == 1  # DiGraph: one edge per pair


class TestVectorIndexEdgeCases:
    def test_add_empty_vectors(self):
        vi = VectorIndex()
        vi.add_entity_vectors([], [])
        vi.add_chunk_vectors([], [])
        assert vi.search_entities([1, 0]) == []

    def test_remove_all(self):
        vi = VectorIndex()
        vi.add_entity_vectors(["a", "b"], [[1, 0], [0, 1]])
        vi.remove_by_ids({"a", "b"})
        assert vi.search_entities([1, 0]) == []

    def test_search_topk_larger_than_count(self):
        vi = VectorIndex()
        vi.add_entity_vectors(["a", "b"], [[1, 0], [0, 1]])
        results = vi.search_entities([1, 0], top_k=100)
        assert len(results) == 2

    def test_cosine_similarity_correctness(self):
        vi = VectorIndex()
        vi.add_entity_vectors(["same", "ortho", "opposite"],
                              [[1, 0], [0, 1], [-1, 0]])
        results = vi.search_entities([1, 0], top_k=3)
        # "same" should be first (score ~1.0)
        assert results[0][0] == "same"
        assert results[0][1] > 0.99

    def test_chunk_search_independent(self):
        vi = VectorIndex()
        vi.add_entity_vectors(["e1", "e2"], [[1, 0], [0, 1]])
        vi.add_chunk_vectors(["c1", "c2"], [[0, 1], [1, 0]])
        # Entity search should not return chunks
        e_results = vi.search_entities([0, 1], top_k=1)
        assert e_results[0][0] == "e2"
        c_results = vi.search_chunks([0, 1], top_k=1)
        assert c_results[0][0] == "c1"
