"""Tests for matrixone_graph/__init__.py — main facade API."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from matrixone_graph import MatrixoneGraph


class TestMatrixoneGraphInit:
    """Tests for MatrixoneGraph initialization."""

    def test_init_with_path(self, tmp_path):
        """Should initialize with repo path."""
        mg = MatrixoneGraph(tmp_path)
        assert mg.repo_path == tmp_path

    def test_init_with_embedding_url(self, tmp_path):
        """Should accept custom embedding URL."""
        mg = MatrixoneGraph(tmp_path, embedding_url="http://custom:3002")
        assert mg._embedder.base_url == "http://custom:3002"

    def test_init_with_str_path(self, tmp_path):
        """Should accept string path."""
        mg = MatrixoneGraph(str(tmp_path))
        assert mg.repo_path == tmp_path


class TestMatrixoneGraphQuery:
    """Tests for MatrixoneGraph.query method."""

    @pytest.mark.asyncio
    async def test_query_returns_result(self, tmp_path):
        """Query should return QueryResult."""
        mg = MatrixoneGraph(tmp_path)

        mock_result = {
            "entities": [],
            "relations": [],
            "context": "Test context",
        }

        with patch("matrixone_graph.query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = mock_result
            result = await mg.query("test query")

        assert result is not None

    @pytest.mark.asyncio
    async def test_query_with_top_k(self, tmp_path):
        """Query should pass top_k parameter."""
        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = {"entities": [], "context": ""}
            await mg.query("test", top_k=5)

            call_kwargs = mock_query.call_args[1]
            assert call_kwargs["top_k"] == 5

    @pytest.mark.asyncio
    async def test_query_with_depth(self, tmp_path):
        """Query should pass depth parameter."""
        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = {"entities": [], "context": ""}
            await mg.query("test", depth=2)

            call_kwargs = mock_query.call_args[1]
            assert call_kwargs["depth"] == 2

    @pytest.mark.asyncio
    async def test_query_with_direction(self, tmp_path):
        """Query should pass direction parameter."""
        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = {"entities": [], "context": ""}
            await mg.query("test", direction="callers")

            call_kwargs = mock_query.call_args[1]
            assert call_kwargs["direction"] == "callers"


class TestMatrixoneGraphIndex:
    """Tests for MatrixoneGraph.status method."""

    def test_status_unindexed(self, tmp_path):
        """Status of a fresh (unindexed) repo should return indexed=False."""
        mg = MatrixoneGraph(tmp_path)
        result = mg.status()
        assert isinstance(result, dict)
        assert result["indexed"] is False

    def test_status_has_required_keys(self, tmp_path):
        """Status should always have an 'indexed' key."""
        mg = MatrixoneGraph(tmp_path)
        result = mg.status()
        assert "indexed" in result


class TestMatrixoneGraphClose:
    """Tests for MatrixoneGraph.close method."""

    @pytest.mark.asyncio
    async def test_close_cleans_up(self, tmp_path):
        """Close should clean up resources."""
        mg = MatrixoneGraph(tmp_path)

        # Should not raise
        await mg.close()

    @pytest.mark.asyncio
    async def test_context_manager(self, tmp_path):
        """Should support explicit close without raising."""
        mg = MatrixoneGraph(tmp_path)
        assert mg.repo_path == tmp_path
        await mg.close()  # Should not raise


class TestMatrixoneGraphGraph:
    """Tests for MatrixoneGraph.query method (used for symbol traversal)."""

    @pytest.mark.asyncio
    async def test_graph_returns_relations(self, tmp_path):
        """Query should return a QueryResult."""
        mg = MatrixoneGraph(tmp_path)

        from matrixone_graph.pipeline import QueryResult
        mock_result = QueryResult(entities=[], relations=[], context="TestClass context")

        with patch("matrixone_graph.query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = mock_result
            result = await mg.query("TestClass")

        assert result is not None


class TestMatrixoneGraphImpact:
    """Tests for MatrixoneGraph.impact_commit method."""

    def test_impact_returns_analysis(self, tmp_path):
        """impact_commit should return a dict with commit key."""
        mg = MatrixoneGraph(tmp_path)

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "commit": "HEAD",
            "changed_symbols": [],
            "affected_modules": [],
        }

        with patch.object(mg, "_load_graph", return_value=MagicMock()):
            with patch("matrixone_graph.impact.ImpactAnalyzer") as MockIA:
                MockIA.return_value.analyze_commit.return_value = mock_result
                result = mg.impact_commit()

        assert result["commit"] == "HEAD"

    def test_impact_with_commit(self, tmp_path):
        """impact_commit should accept a commit hash."""
        mg = MatrixoneGraph(tmp_path)

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"commit": "abc123", "changed_symbols": []}

        with patch.object(mg, "_load_graph", return_value=MagicMock()):
            with patch("matrixone_graph.impact.ImpactAnalyzer") as MockIA:
                MockIA.return_value.analyze_commit.return_value = mock_result
                result = mg.impact_commit("abc123")

        MockIA.return_value.analyze_commit.assert_called_once_with("abc123")


class TestMatrixoneGraphCodeHealth:
    """Tests for MatrixoneGraph.health method."""

    @pytest.mark.asyncio
    async def test_code_health_returns_metrics(self, tmp_path):
        """health() should return a dict with score key."""
        mg = MatrixoneGraph(tmp_path)

        with patch.object(mg, "_load_graph", return_value=MagicMock()):
            with patch("matrixone_graph.health.compute_graph_metrics", return_value={}):
                with patch("matrixone_graph.health.scan_directory_debt", return_value={}):
                    with patch("matrixone_graph.health.compute_score", return_value={"score": 85, "grade": "A"}):
                        result = await mg.health()

        assert result["score"] == 85
