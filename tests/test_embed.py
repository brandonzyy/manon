"""Tests for matrixone_graph/embed.py — EmbeddingClient."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from matrixone_graph.embed import EmbeddingClient


class TestEmbeddingClientInit:
    def test_defaults(self):
        ec = EmbeddingClient()
        assert ec.base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert ec.batch_size == 32
        assert ec.timeout == 30.0

    def test_custom(self):
        ec = EmbeddingClient(base_url="http://custom:9090/", batch_size=16, timeout=10.0)
        assert ec.base_url == "http://custom:9090"  # trailing slash stripped
        assert ec.batch_size == 16

    def test_client_initially_none(self):
        ec = EmbeddingClient()
        assert ec._client is None


class TestEmbeddingClientClose:
    @pytest.mark.asyncio
    async def test_close_no_client(self):
        ec = EmbeddingClient()
        await ec.close()  # should not raise

    @pytest.mark.asyncio
    async def test_close_with_client(self):
        ec = EmbeddingClient()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        ec._client = mock_client
        await ec.close()
        mock_client.aclose.assert_called_once()
        assert ec._client is None


class TestEmbeddingClientEmbed:
    @pytest.mark.asyncio
    async def test_embed_list_format(self):
        ec = EmbeddingClient(batch_size=2)
        mock_resp = MagicMock()
        mock_resp.json.return_value = [[0.1, 0.2], [0.3, 0.4]]
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False
        ec._client = mock_client
        result = await ec.embed(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_embed_openai_format(self):
        ec = EmbeddingClient(batch_size=2)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"embedding": [0.5, 0.6]}]}
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False
        ec._client = mock_client
        result = await ec.embed(["test"])
        assert result == [[0.5, 0.6]]

    @pytest.mark.asyncio
    async def test_embed_single(self):
        ec = EmbeddingClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [[0.1, 0.2]]
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False
        ec._client = mock_client
        result = await ec.embed_single("hello")
        assert result == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_embed_batching(self):
        ec = EmbeddingClient(batch_size=2)
        call_count = 0
        mock_resp = MagicMock()
        mock_resp.json.return_value = [[0.1], [0.2]]
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False
        ec._client = mock_client
        result = await ec.embed(["a", "b", "c", "d"])
        assert mock_client.post.call_count == 2  # 4 texts / batch_size 2 = 2 calls
