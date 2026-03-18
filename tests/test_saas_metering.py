"""Tests for saas/metering.py — usage and query logging."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from saas.metering import record_usage, record_query


class TestRecordUsage:
    """Tests for record_usage function."""

    @pytest.mark.asyncio
    async def test_record_usage_basic(self):
        """Should record basic usage entry."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_usage(
                tenant_id="tenant-1",
                endpoint="search",
            )

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_usage_with_repo(self):
        """Should record usage with repo_id."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_usage(
                tenant_id="tenant-1",
                endpoint="graph",
                repo_id="repo-123",
            )

        call_args = mock_db.execute.call_args
        assert "repo_id" in call_args[0][0].lower()
        assert call_args[0][1][2] == "repo-123"  # repo_id parameter

    @pytest.mark.asyncio
    async def test_record_usage_with_tokens(self):
        """Should record usage with token count."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_usage(
                tenant_id="tenant-1",
                endpoint="deep_query",
                tokens=500,
            )

        call_args = mock_db.execute.call_args
        assert call_args[0][1][3] == 500  # tokens parameter

    @pytest.mark.asyncio
    async def test_record_usage_full_params(self):
        """Should record with all parameters."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_usage(
                tenant_id="tenant-1",
                endpoint="search",
                repo_id="repo-1",
                tokens=1000,
            )

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert params[0] == "tenant-1"
        assert params[1] == "search"
        assert params[2] == "repo-1"
        assert params[3] == 1000


class TestRecordQuery:
    """Tests for record_query function."""

    @pytest.mark.asyncio
    async def test_record_query_basic(self):
        """Should record basic query entry."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_query(
                tenant_id="tenant-1",
                repo_id="repo-1",
                endpoint="search",
                query="authentication flow",
            )

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_query_with_rounds(self):
        """Should record with rounds count."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_query(
                tenant_id="tenant-1",
                repo_id="repo-1",
                endpoint="deep_query",
                query="complex query",
                rounds=3,
            )

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert params[4] == 3  # rounds

    @pytest.mark.asyncio
    async def test_record_query_with_rounds_detail(self):
        """Should record with rounds_detail JSON."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        rounds_detail = [
            {"round": 0, "query": "original", "entities": ["e1"], "covered": False},
            {"round": 1, "query": "followup", "entities": ["e2"], "covered": True},
        ]

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_query(
                tenant_id="tenant-1",
                repo_id="repo-1",
                endpoint="deep_query",
                query="test query",
                rounds=2,
                rounds_detail=rounds_detail,
            )

        call_args = mock_db.execute.call_args
        import json
        params = call_args[0][1]
        assert params[5] is not None  # rounds_detail
        parsed = json.loads(params[5])
        assert len(parsed) == 2
        assert parsed[0]["round"] == 0

    @pytest.mark.asyncio
    async def test_record_query_with_coverage(self):
        """Should record with coverage score."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_query(
                tenant_id="tenant-1",
                repo_id="repo-1",
                endpoint="search",
                query="test",
                coverage=0.85,
            )

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert params[6] == 0.85  # coverage

    @pytest.mark.asyncio
    async def test_record_query_handles_exception(self):
        """Should handle exceptions gracefully."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB error"))

        with patch("saas.metering.get_db", return_value=mock_db):
            # Should not raise
            await record_query(
                tenant_id="tenant-1",
                repo_id="repo-1",
                endpoint="search",
                query="test",
            )

    @pytest.mark.asyncio
    async def test_record_query_unicode(self):
        """Should handle unicode queries correctly."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_query(
                tenant_id="tenant-1",
                repo_id="repo-1",
                endpoint="search",
                query="用户认证流程",  # Chinese characters
                rounds_detail=[{"round": 0, "query": "中文测试"}],
            )

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert "用户认证流程" in params[3]


class TestMeteringIntegration:
    """Integration tests for metering module."""

    @pytest.mark.asyncio
    async def test_usage_log_table_structure(self):
        """Verify usage_log insert has correct columns."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_usage(tenant_id="t1", endpoint="test")

        sql = mock_db.execute.call_args[0][0]
        assert "usage_log" in sql.lower()
        assert "tenant_id" in sql.lower()
        assert "endpoint" in sql.lower()

    @pytest.mark.asyncio
    async def test_query_log_table_structure(self):
        """Verify query_log insert has correct columns."""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch("saas.metering.get_db", return_value=mock_db):
            await record_query(
                tenant_id="t1",
                repo_id="r1",
                endpoint="search",
                query="test",
            )

        sql = mock_db.execute.call_args[0][0]
        assert "query_log" in sql.lower()
        assert "tenant_id" in sql.lower()
        assert "repo_id" in sql.lower()
        assert "endpoint" in sql.lower()
        assert "query" in sql.lower()
