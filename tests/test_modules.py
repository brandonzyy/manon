"""Tests for matrixone_graph — MatrixoneGraph facade, embed client, CLI."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from matrixone_graph import MatrixoneGraph, QueryResult
from matrixone_graph.embed import EmbeddingClient
from matrixone_graph.merge_dynamic import (
    DYNAMIC_FILE_PATH, merge_dynamic_edges, load_dynamic_deps,
)
from matrixone_graph.store import CodeGraph, Entity, Relation


# ── MatrixoneGraph facade ─────────────────────────────

class TestMatrixoneGraph:
    def test_configure(self):
        MatrixoneGraph.configure(embedding_url="http://test:8080")
        assert MatrixoneGraph._embedding_url == "http://test:8080"

    def test_repo_key(self):
        key = MatrixoneGraph._repo_key(Path("/tmp/myrepo"))
        assert "myrepo" in key
        assert len(key) > len("myrepo")

    def test_get_creates_instance(self, tmp_path):
        MatrixoneGraph._pool.clear()
        mg = MatrixoneGraph.get(tmp_path)
        assert mg is not None
        # Same path returns same instance
        mg2 = MatrixoneGraph.get(tmp_path)
        assert mg is mg2
        MatrixoneGraph._pool.clear()

    def test_status(self, tmp_path):
        MatrixoneGraph._pool.clear()
        mg = MatrixoneGraph.get(tmp_path)
        st = mg.status()
        assert isinstance(st, dict)
        MatrixoneGraph._pool.clear()


# ── EmbeddingClient ───────────────────────────────────

class TestEmbeddingClient:
    def test_init(self):
        ec = EmbeddingClient(base_url="http://localhost:8080")
        assert ec.base_url == "http://localhost:8080"

    @pytest.mark.asyncio
    async def test_close(self):
        ec = EmbeddingClient(base_url="http://localhost:8080")
        await ec.close()  # should not raise


# ── merge_dynamic ─────────────────────────────────────

class TestMergeDynamic:
    def test_dynamic_file_path_constant(self):
        assert DYNAMIC_FILE_PATH == "__dynamic__"

    def test_merge_empty(self):
        g = CodeGraph()
        stats = merge_dynamic_edges(g, {})
        assert stats["added"] == 0

    def test_merge_with_existing_entity(self):
        g = CodeGraph()
        g.add_entity(Entity(id="mod.foo", kind="function", name="foo"))
        g.add_entity(Entity(id="mod.bar", kind="function", name="bar"))
        stats = merge_dynamic_edges(g, {"mod.foo->mod.bar": 3})
        assert stats["added"] == 1
        assert stats["skipped"] == 0

    def test_merge_skips_unknown(self):
        g = CodeGraph()
        stats = merge_dynamic_edges(g, {"unknown.a->unknown.b": 1})
        assert stats["added"] == 0
        assert stats["skipped"] == 1

    def test_load_dynamic_deps(self, tmp_path):
        p = tmp_path / "deps.json"
        p.write_text('{"a->b": 1}', encoding="utf-8")
        data = load_dynamic_deps(p)
        assert data == {"a->b": 1}

    def test_replace_removes_old(self):
        g = CodeGraph()
        g.add_entity(Entity(id="a", kind="function", name="a"))
        g.add_entity(Entity(id="b", kind="function", name="b"))
        merge_dynamic_edges(g, {"a->b": 1})
        assert g.relation_count == 1
        # Replace should remove old dynamic edges first
        merge_dynamic_edges(g, {"a->b": 2}, replace=True)
        assert g.relation_count == 1


# ── saas models ───────────────────────────────────────

class TestSaasModels:
    def test_import_models(self):
        from saas.models import (
            IndexStatus, SyncAstRequest, FileSyncData,
            RepoCreate, RepoOut, SearchResult, DeepQueryRequest,
            MergeDynamicRequest,
        )
        # Verify defaults
        m = MergeDynamicRequest()
        assert m.edges == {}
        assert m.raw_edges == []

    def test_file_sync_data(self):
        from saas.models import FileSyncData
        f = FileSyncData(rel_path="a.py", hash="abc", parse_result={})
        assert f.rel_path == "a.py"

    def test_repo_create(self):
        from saas.models import RepoCreate
        r = RepoCreate(name="test")
        assert r.name == "test"
        assert r.branch == "main"
