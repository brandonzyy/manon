"""Tests for core.saas_client configure, helpers, and API wrappers."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from core import saas_client


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
        assert saas_client._saas_url == "http://example:3700"
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
        with patch("core.saas_client._post", new_callable=AsyncMock) as mock:
            mock.return_value = {"id": "r1", "name": "test"}
            result = await saas_client.repos_create("test", branch="dev")
            mock.assert_called_once()
            assert result["id"] == "r1"

    @pytest.mark.asyncio
    async def test_repos_list(self):
        with patch("core.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = [{"id": "r1"}]
            result = await saas_client.repos_list()
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_repos_get(self):
        with patch("core.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"id": "r1", "name": "test"}
            result = await saas_client.repos_get("r1")
            assert result["name"] == "test"

    @pytest.mark.asyncio
    async def test_repos_delete(self):
        with patch("core.saas_client._delete", new_callable=AsyncMock) as mock:
            await saas_client.repos_delete("r1")
            mock.assert_called_once_with("/api/v1/repos/r1")


class TestQueryAPIs:
    @pytest.mark.asyncio
    async def test_search(self):
        with patch("core.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"entities": [], "context": ""}
            result = await saas_client.search("r1", "auth flow", top_k=5)
            assert "entities" in result

    @pytest.mark.asyncio
    async def test_graph(self):
        with patch("core.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"nodes": [], "edges": []}
            result = await saas_client.graph("r1", "Foo", depth=2)
            assert "nodes" in result

    @pytest.mark.asyncio
    async def test_impact(self):
        with patch("core.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"commit": "abc", "changed_symbols": []}
            result = await saas_client.impact("r1", commit="HEAD")
            assert result["commit"] == "abc"


class TestIndexingAPIs:
    @pytest.mark.asyncio
    async def test_sync_ast(self):
        with patch("core.saas_client._post", new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "ok"}
            result = await saas_client.sync_ast("r1", [], [])
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_push_update(self):
        with patch("core.saas_client._post", new_callable=AsyncMock) as mock:
            mock.return_value = {"updated": True}
            result = await saas_client.push_update("r1")
            assert result["updated"]

    @pytest.mark.asyncio
    async def test_index_status(self):
        with patch("core.saas_client._get", new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "done"}
            result = await saas_client.index_status("r1")
            assert result["status"] == "done"


# ── Error Handling Tests ─────────────────────────────────────

class TestHttpErrors:
    """Tests for HTTP error handling in saas_client."""

    @pytest.mark.asyncio
    async def test_get_http_error_4xx(self):
        """_get should raise on 4xx response."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            # Mock raise_for_status to raise HTTPStatusError
            def raise_error():
                raise httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=mock_response)
            mock_response.raise_for_status = raise_error

            with pytest.raises(httpx.HTTPStatusError):
                await saas_client._get("/api/v1/repos")

    @pytest.mark.asyncio
    async def test_get_http_error_5xx(self):
        """_get should raise on 5xx response."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            def raise_error():
                raise httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_response)
            mock_response.raise_for_status = raise_error

            with pytest.raises(httpx.HTTPStatusError):
                await saas_client._get("/api/v1/repos")

    @pytest.mark.asyncio
    async def test_post_http_error(self):
        """_post should raise on error response."""
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Validation Error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            def raise_error():
                raise httpx.HTTPStatusError("Validation Error", request=MagicMock(), response=mock_response)
            mock_response.raise_for_status = raise_error

            with pytest.raises(httpx.HTTPStatusError):
                await saas_client._post("/api/v1/repos", {"name": "test"})

    @pytest.mark.asyncio
    async def test_delete_http_error(self):
        """_delete should raise on error response."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.delete = AsyncMock()
            mock_instance.delete.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            def raise_error():
                raise httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)
            mock_response.raise_for_status = raise_error

            with pytest.raises(httpx.HTTPStatusError):
                await saas_client._delete("/api/v1/repos/nonexistent")


class TestTimeoutHandling:
    """Tests for timeout handling in saas_client."""

    @pytest.mark.asyncio
    async def test_get_timeout(self):
        """_get should handle timeout."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get = AsyncMock()
            mock_instance.get.side_effect = httpx.TimeoutException("Request timed out")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(httpx.TimeoutException):
                await saas_client._get("/api/v1/repos")

    @pytest.mark.asyncio
    async def test_post_timeout(self):
        """_post should handle timeout."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post = AsyncMock()
            mock_instance.post.side_effect = httpx.TimeoutException("Request timed out")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(httpx.TimeoutException):
                await saas_client._post("/api/v1/repos", {"name": "test"})


class TestConnectionError:
    """Tests for connection error handling."""

    @pytest.mark.asyncio
    async def test_connection_error(self):
        """_get should handle connection error."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get = AsyncMock()
            mock_instance.get.side_effect = httpx.ConnectError("Connection refused")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(httpx.ConnectError):
                await saas_client._get("/api/v1/repos")


class TestHealthEndpoint:
    """Tests for health endpoint."""

    @pytest.mark.asyncio
    async def test_health_success(self):
        """health() should return health status."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await saas_client.health()
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_timeout(self):
        """health() should handle timeout."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get = AsyncMock()
            mock_instance.get.side_effect = httpx.TimeoutException("Timeout")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with pytest.raises(httpx.TimeoutException):
                await saas_client.health()


class TestCustomTimeout:
    """Tests for custom timeout parameter."""

    @pytest.mark.asyncio
    async def test_custom_timeout_passed(self):
        """_get should pass custom timeout."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await saas_client._get("/api/v1/repos", timeout=120)

            # Check that AsyncClient was created with timeout=120
            call_kwargs = mock_client.call_args[1]
            assert call_kwargs["timeout"] == 120
