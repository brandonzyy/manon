"""Extended tests for matrixone_graph/__init__.py — MatrixoneGraph facade."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from matrixone_graph import MatrixoneGraph, IndexResult, QueryResult
from matrixone_graph.store import CodeGraph


class TestMatrixoneGraphConfigure:
    def test_configure_embedding_url(self):
        old = MatrixoneGraph._embedding_url
        MatrixoneGraph.configure(embedding_url="http://new:8080")
        assert MatrixoneGraph._embedding_url == "http://new:8080"
        MatrixoneGraph._embedding_url = old

    def test_configure_data_dir(self, tmp_path):
        old = MatrixoneGraph._data_dir
        MatrixoneGraph.configure(data_dir=str(tmp_path / "indexes"))
        assert MatrixoneGraph._data_dir is not None
        assert MatrixoneGraph._data_dir.exists()
        MatrixoneGraph._data_dir = old

    def test_configure_empty_no_change(self):
        old_url = MatrixoneGraph._embedding_url
        old_dir = MatrixoneGraph._data_dir
        MatrixoneGraph.configure()
        assert MatrixoneGraph._embedding_url == old_url
        assert MatrixoneGraph._data_dir == old_dir


class TestMatrixoneGraphPool:
    def test_get_creates_instance(self, tmp_path):
        MatrixoneGraph._pool.clear()
        mg = MatrixoneGraph.get(tmp_path)
        assert mg is not None
        assert mg.repo_path == tmp_path.resolve()
        MatrixoneGraph._pool.clear()

    def test_get_returns_same_instance(self, tmp_path):
        MatrixoneGraph._pool.clear()
        mg1 = MatrixoneGraph.get(tmp_path)
        mg2 = MatrixoneGraph.get(tmp_path)
        assert mg1 is mg2
        MatrixoneGraph._pool.clear()

    def test_different_paths_different_instances(self, tmp_path):
        MatrixoneGraph._pool.clear()
        p1 = tmp_path / "repo1"
        p2 = tmp_path / "repo2"
        p1.mkdir()
        p2.mkdir()
        mg1 = MatrixoneGraph.get(p1)
        mg2 = MatrixoneGraph.get(p2)
        assert mg1 is not mg2
        MatrixoneGraph._pool.clear()


class TestRepoKey:
    def test_contains_name(self):
        key = MatrixoneGraph._repo_key(Path("/tmp/myrepo"))
        assert "myrepo" in key

    def test_unique_for_different_paths(self):
        k1 = MatrixoneGraph._repo_key(Path("/a/repo"))
        k2 = MatrixoneGraph._repo_key(Path("/b/repo"))
        assert k1 != k2

    def test_deterministic(self):
        k1 = MatrixoneGraph._repo_key(Path("/tmp/test"))
        k2 = MatrixoneGraph._repo_key(Path("/tmp/test"))
        assert k1 == k2


class TestMatrixoneGraphStatus:
    def test_status_unindexed(self, tmp_path):
        MatrixoneGraph._pool.clear()
        mg = MatrixoneGraph.get(tmp_path)
        st = mg.status()
        assert isinstance(st, dict)
        assert st.get("indexed") is False
        MatrixoneGraph._pool.clear()


class TestLoadGraph:
    def test_load_empty(self, tmp_path):
        MatrixoneGraph._pool.clear()
        mg = MatrixoneGraph.get(tmp_path)
        g = mg._load_graph()
        assert isinstance(g, CodeGraph)
        assert g.entity_count == 0
        MatrixoneGraph._pool.clear()


class TestDataclasses:
    def test_index_result_defaults(self):
        r = IndexResult()
        assert r.files_scanned == 0
        assert r.entities_added == 0
        assert r.relations_added == 0
        assert r.chunks_added == 0
        assert r.files_skipped == 0

    def test_query_result_defaults(self):
        r = QueryResult()
        assert r.entities == []
        assert r.relations == []
        assert r.chunks == []
        assert r.context == ""
