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
        assert mg._embedding_url == "http://custom:3002"

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
    """Tests for MatrixoneGraph.index_repo method."""

    @pytest.mark.asyncio
    async def test_index_repo_basic(self, tmp_path):
        """Index should process repo files."""
        # Create some source files
        (tmp_path / "main.py").write_text("def main(): pass")

        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.index_repo", new_callable=AsyncMock) as mock_index:
            mock_index.return_value = {"files": 1, "entities": 2, "relations": 1}
            result = await mg.index_repo()

        assert result is not None

    @pytest.mark.asyncio
    async def test_index_repo_incremental(self, tmp_path):
        """Index should support incremental mode."""
        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.index_repo", new_callable=AsyncMock) as mock_index:
            mock_index.return_value = {"files": 0}
            await mg.index_repo(incremental=True)

            mock_index.assert_called_once()


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
        """Should work as async context manager."""
        async with MatrixoneGraph(tmp_path) as mg:
            assert mg.repo_path == tmp_path


class TestMatrixoneGraphGraph:
    """Tests for MatrixoneGraph.graph method."""

    @pytest.mark.asyncio
    async def test_graph_returns_relations(self, tmp_path):
        """Graph should return symbol relations."""
        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.graph", new_callable=AsyncMock) as mock_graph:
            mock_graph.return_value = {
                "symbol": "TestClass",
                "relations": [],
            }
            result = await mg.graph("TestClass")

        assert "symbol" in result


class TestMatrixoneGraphImpact:
    """Tests for MatrixoneGraph.impact method."""

    @pytest.mark.asyncio
    async def test_impact_returns_analysis(self, tmp_path):
        """Impact should return analysis result."""
        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.impact", new_callable=AsyncMock) as mock_impact:
            mock_impact.return_value = {
                "commit": "HEAD",
                "changed_symbols": [],
                "affected_modules": [],
            }
            result = await mg.impact()

        assert result["commit"] == "HEAD"

    @pytest.mark.asyncio
    async def test_impact_with_commit(self, tmp_path):
        """Impact should accept commit hash."""
        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.impact", new_callable=AsyncMock) as mock_impact:
            mock_impact.return_value = {"commit": "abc123", "changed_symbols": []}
            await mg.impact(commit="abc123")

            call_kwargs = mock_impact.call_args[1]
            assert call_kwargs["commit"] == "abc123"


class TestMatrixoneGraphCodeHealth:
    """Tests for MatrixoneGraph.code_health method."""

    @pytest.mark.asyncio
    async def test_code_health_returns_metrics(self, tmp_path):
        """Code health should return metrics."""
        mg = MatrixoneGraph(tmp_path)

        with patch("matrixone_graph.health", new_callable=AsyncMock) as mock_health:
            mock_health.return_value = {
                "score": 85,
                "grade": "A",
                "dimensions": [],
            }
            result = await mg.code_health()

        assert result["score"] == 85
