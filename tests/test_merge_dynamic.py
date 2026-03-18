"""Tests for matrixone_graph/merge_dynamic.py — dynamic edge merging."""
import pytest
import tempfile
import json
from pathlib import Path

from matrixone_graph.merge_dynamic import (
    _compute_weight,
    _remove_dynamic_edges,
    load_dynamic_deps,
    merge_dynamic_edges,
    DYNAMIC_FILE_PATH,
)
from matrixone_graph.store import CodeGraph, Entity, EntityKind


class TestComputeWeight:
    """Tests for _compute_weight function."""

    def test_weight_count_1(self):
        """count=1 should give weight=1.0 (log2(1) = 0)."""
        assert _compute_weight(1) == 1.0

    def test_weight_count_2(self):
        """count=2 should give weight=2.0 (1 + log2(2) = 1 + 1 = 2)."""
        assert _compute_weight(2) == 2.0

    def test_weight_count_4(self):
        """count=4 should give weight=3.0 (1 + log2(4) = 1 + 2 = 3)."""
        assert _compute_weight(4) == 3.0

    def test_weight_count_8(self):
        """count=8 should give weight=4.0 (1 + log2(8) = 1 + 3 = 4)."""
        assert _compute_weight(8) == 4.0

    def test_weight_count_16(self):
        """count=16 should give weight=5.0 (1 + log2(16) = 1 + 4 = 5)."""
        assert _compute_weight(16) == 5.0

    def test_weight_caps_at_5(self):
        """Weight should cap at 5.0."""
        assert _compute_weight(64) == 5.0
        assert _compute_weight(128) == 5.0
        assert _compute_weight(1000000) == 5.0

    def test_weight_count_0_edge_case(self):
        """count=0 should be treated as 1 (max(count, 1))."""
        assert _compute_weight(0) == 1.0

    def test_weight_negative_edge_case(self):
        """Negative count should be treated as 1."""
        assert _compute_weight(-5) == 1.0

    def test_weight_fractional_count(self):
        """Fractional count should work with log2."""
        # log2(1.5) ≈ 0.585
        result = _compute_weight(1.5)
        assert 1.5 < result < 1.7


class TestLoadDynamicDeps:
    """Tests for load_dynamic_deps function."""

    def test_load_valid_json(self, tmp_path):
        """Should load valid JSON file."""
        deps_file = tmp_path / "dynamic-deps.json"
        deps_file.write_text(json.dumps({"a->b": 5, "c->d": 3}))

        result = load_dynamic_deps(deps_file)
        assert result == {"a->b": 5, "c->d": 3}

    def test_load_empty_json(self, tmp_path):
        """Should handle empty JSON object."""
        deps_file = tmp_path / "dynamic-deps.json"
        deps_file.write_text("{}")

        result = load_dynamic_deps(deps_file)
        assert result == {}

    def test_load_invalid_json(self, tmp_path):
        """Should raise on invalid JSON."""
        deps_file = tmp_path / "dynamic-deps.json"
        deps_file.write_text("not valid json")

        with pytest.raises(json.JSONDecodeError):
            load_dynamic_deps(deps_file)

    def test_load_nonexistent_file(self, tmp_path):
        """Should raise on nonexistent file."""
        with pytest.raises(FileNotFoundError):
            load_dynamic_deps(tmp_path / "nonexistent.json")


class TestRemoveDynamicEdges:
    """Tests for _remove_dynamic_edges function."""

    def test_remove_no_dynamic_edges(self):
        """Should return 0 if no dynamic edges."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))

        # Add static edge
        graph.add_relation(graph._create_relation("a", "b", "calls", file_path="test.py"))

        removed = _remove_dynamic_edges(graph)
        assert removed == 0

    def test_remove_dynamic_edges(self):
        """Should remove dynamic edges."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))

        # Add dynamic edge
        graph.add_relation(graph._create_relation("a", "b", "calls", file_path=DYNAMIC_FILE_PATH))

        removed = _remove_dynamic_edges(graph)
        assert removed == 1
        assert graph._g.number_of_edges() == 0

    def test_remove_mixed_edges(self):
        """Should only remove dynamic edges, keep static."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))

        # Add both types
        graph.add_relation(graph._create_relation("a", "b", "calls", file_path="test.py"))
        graph.add_relation(graph._create_relation("b", "a", "calls", file_path=DYNAMIC_FILE_PATH))

        removed = _remove_dynamic_edges(graph)
        assert removed == 1
        assert graph._g.number_of_edges() == 1


class TestMergeDynamicEdges:
    """Tests for merge_dynamic_edges function."""

    def test_merge_basic(self):
        """Basic merge should add edges."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="mod.func_a", name="func_a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="mod.func_b", name="func_b", kind=EntityKind.FUNCTION, file_path="test.py"))

        edges = {"mod.func_a->mod.func_b": 5}

        result = merge_dynamic_edges(graph, edges)
        assert result["added"] == 1
        assert result["skipped"] == 0

    def test_merge_replaces_existing(self):
        """replace=True should remove old dynamic edges first."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))

        # Add old dynamic edge
        graph.add_relation(graph._create_relation("a", "b", "calls", file_path=DYNAMIC_FILE_PATH))

        edges = {"a->b": 10}
        result = merge_dynamic_edges(graph, edges, replace=True)

        assert result["removed"] == 1
        assert result["added"] == 1

    def test_merge_appends(self):
        """replace=False should keep old dynamic edges."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))

        # Add old dynamic edge
        graph.add_relation(graph._create_relation("a", "b", "calls", file_path=DYNAMIC_FILE_PATH))

        edges = {"b->a": 5}
        result = merge_dynamic_edges(graph, edges, replace=False)

        assert result["removed"] == 0
        assert result["added"] == 1

    def test_merge_skips_invalid_edge_key(self):
        """Invalid edge key (no ->) should be skipped."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))

        edges = {"invalid_key": 5, "also_invalid": 3}
        result = merge_dynamic_edges(graph, edges)

        assert result["added"] == 0
        assert result["skipped"] == 2

    def test_merge_skips_missing_entities(self):
        """Edge with neither endpoint existing should be skipped."""
        graph = CodeGraph()

        edges = {"nonexistent_a->nonexistent_b": 5}
        result = merge_dynamic_edges(graph, edges)

        assert result["added"] == 0
        assert result["skipped"] == 1

    def test_merge_adds_if_one_endpoint_exists(self):
        """Edge with at least one endpoint existing should be added."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))

        edges = {"a->nonexistent_b": 5}
        result = merge_dynamic_edges(graph, edges)

        # Should add because src exists
        assert result["added"] == 1
        assert result["skipped"] == 0

    def test_merge_weight_in_edge(self):
        """Merged edge should have correct weight."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))

        edges = {"a->b": 8}  # weight should be 4.0
        merge_dynamic_edges(graph, edges)

        # Check edge weight
        edge_data = graph._g.get_edge_data("a", "b")
        assert edge_data["weight"] == 4.0

    def test_merge_dynamic_file_path_marker(self):
        """Merged edge should have __dynamic__ file_path."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))

        edges = {"a->b": 5}
        merge_dynamic_edges(graph, edges)

        edge_data = graph._g.get_edge_data("a", "b")
        assert edge_data["file_path"] == DYNAMIC_FILE_PATH

    def test_merge_multiple_edges(self):
        """Multiple edges should all be processed."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="c", name="c", kind=EntityKind.FUNCTION, file_path="test.py"))

        edges = {"a->b": 5, "b->c": 3, "invalid": 1}
        result = merge_dynamic_edges(graph, edges)

        assert result["added"] == 2
        assert result["skipped"] == 1

    def test_merge_empty_edges(self):
        """Empty edges dict should return zeros."""
        graph = CodeGraph()

        result = merge_dynamic_edges(graph, {})
        assert result["added"] == 0
        assert result["skipped"] == 0
        assert result["removed"] == 0


class TestDynamicEdgeProperties:
    """Tests for dynamic edge properties."""

    def test_edge_has_dynamic_description(self):
        """Dynamic edge should have descriptive text."""
        graph = CodeGraph()
        graph.add_entity(Entity(id="a", name="a", kind=EntityKind.FUNCTION, file_path="test.py"))
        graph.add_entity(Entity(id="b", name="b", kind=EntityKind.FUNCTION, file_path="test.py"))

        edges = {"a->b": 42}
        merge_dynamic_edges(graph, edges)

        edge_data = graph._g.get_edge_data("a", "b")
        assert "[dynamic]" in edge_data["description"]
        assert "count=42" in edge_data["description"]
