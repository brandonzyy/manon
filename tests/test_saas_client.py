"""Tests for shared/saas_client — configure, helpers, API wrappers."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from shared import saas_client


class TestConfigure:
    def test_configure_sets_url(self):
        old = saas_client._saas_url
        saas_client.configure("http://test:9999", "key123")
        assert saas_client._saas_url == "http://test:9999"
        assert saas_client._api_key == "key123"
        saas_client.configure(old)  # restore

    def test_configure_strips_trailing_slash(self):
        old = saas_client._saas_url
        saas_client.configure("http://test:9999/")
        assert saas_client._saas_url == "http://test:9999"
        saas_client.configure(old)


class TestHelpers:
    def test_base_url(self):
        old = saas_client._saas_url
        saas_client._saas_url = "http://example:3700"
        assert saas_client._base_url() == "http://example:3700"
        saas_client._saas_url = old

    def test_headers(self):
        old = saas_client._api_key
        saas_client._api_key = "test-key"
        h = saas_client._headers()
        assert h["Authorization"] == "Bearer test-key"
        assert h["Content-Type"] == "application/json"
        saas_client._api_key = old


class TestReposCrud:
    @pytest.mark.asyncio
    async def test_repos_create(self):
        with patch("shared.saas_client._post", new_callable=AsyncMock) as mock:
            mock.return_value = {"id": "r1", "name": "test"}
            result = await saas_client.repos_create("test", branch="dev")
            mock.assert_called_once()
            assert result["id"] == "r1"

    @pytest.mark.asyncio
    async def test_repos_list(self):
        with patch("shared.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = [{"id": "r1"}]
            result = await saas_client.repos_list()
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_repos_get(self):
        with patch("shared.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"id": "r1", "name": "test"}
            result = await saas_client.repos_get("r1")
            assert result["name"] == "test"

    @pytest.mark.asyncio
    async def test_repos_delete(self):
        with patch("shared.saas_client._delete", new_callable=AsyncMock) as mock:
            await saas_client.repos_delete("r1")
            mock.assert_called_once_with("/api/v1/repos/r1")


class TestQueryAPIs:
    @pytest.mark.asyncio
    async def test_search(self):
        with patch("shared.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"entities": [], "context": ""}
            result = await saas_client.search("r1", "auth flow", top_k=5)
            assert "entities" in result

    @pytest.mark.asyncio
    async def test_graph(self):
        with patch("shared.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"nodes": [], "edges": []}
            result = await saas_client.graph("r1", "Foo", depth=2)
            assert "nodes" in result

    @pytest.mark.asyncio
    async def test_impact(self):
        with patch("shared.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"commit": "abc", "changed_symbols": []}
            result = await saas_client.impact("r1", commit="HEAD")
            assert result["commit"] == "abc"


class TestIndexingAPIs:
    @pytest.mark.asyncio
    async def test_sync_ast(self):
        with patch("shared.saas_client._post", new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "ok"}
            result = await saas_client.sync_ast("r1", [], [])
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_push_update(self):
        with patch("shared.saas_client._post", new_callable=AsyncMock) as mock:
            mock.return_value = {"updated": True}
            result = await saas_client.push_update("r1")
            assert result["updated"]

    @pytest.mark.asyncio
    async def test_index_status(self):
        with patch("shared.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "done"}
            result = await saas_client.index_status("r1")
            assert result["status"] == "done"

    @pytest.mark.asyncio
    async def test_trigger_index(self):
        with patch("shared.saas_client._post", new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "indexing"}
            result = await saas_client.trigger_index("r1", incremental=False)
            assert result["status"] == "indexing"
