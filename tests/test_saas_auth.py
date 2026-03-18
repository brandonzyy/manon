"""Tests for saas/auth.py — authentication and tenant context."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from saas.auth import TenantContext, require_tenant


class TestTenantContext:
    """Tests for TenantContext dataclass."""

    def test_create_tenant_context(self):
        """Should create context with all fields."""
        ctx = TenantContext(
            tenant_id="tenant-123",
            tier="pro",
            rate_limit=100,
        )
        assert ctx.tenant_id == "tenant-123"
        assert ctx.tier == "pro"
        assert ctx.rate_limit == 100

    def test_tenant_context_default_values(self):
        """Should have required fields only."""
        ctx = TenantContext(tenant_id="t1", tier="free", rate_limit=10)
        assert ctx.tenant_id == "t1"
        assert ctx.tier == "free"
        assert ctx.rate_limit == 10


class TestRequireTenant:
    """Tests for require_tenant dependency."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_context(self):
        """Valid token should return TenantContext."""
        mock_cred = MagicMock(spec=HTTPAuthorizationCredentials)
        mock_cred.credentials = "valid-api-key"

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={
            "tenant_id": "tenant-123",
            "tier": "pro",
        })
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.auth.get_db", return_value=mock_db):
            with patch("saas.auth.limiter.check", return_value=True):
                with patch("saas.auth.settings.rate_for", return_value=100):
                    result = await require_tenant(mock_cred)

        assert isinstance(result, TenantContext)
        assert result.tenant_id == "tenant-123"
        assert result.tier == "pro"

    @pytest.mark.asyncio
    async def test_invalid_token_raises_401(self):
        """Invalid token should raise 401."""
        mock_cred = MagicMock(spec=HTTPAuthorizationCredentials)
        mock_cred.credentials = "invalid-key"

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.auth.get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await require_tenant(mock_cred)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "invalid" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_raises_429(self):
        """Rate limit exceeded should raise 429."""
        mock_cred = MagicMock(spec=HTTPAuthorizationCredentials)
        mock_cred.credentials = "valid-api-key"

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={
            "tenant_id": "tenant-123",
            "tier": "free",
        })
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.auth.get_db", return_value=mock_db):
            with patch("saas.auth.limiter.check", return_value=False):
                with patch("saas.auth.settings.rate_for", return_value=10):
                    with pytest.raises(HTTPException) as exc_info:
                        await require_tenant(mock_cred)

        assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "rate limit" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_inactive_key_raises_401(self):
        """Inactive API key should raise 401."""
        mock_cred = MagicMock(spec=HTTPAuthorizationCredentials)
        mock_cred.credentials = "inactive-key"

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        # Query with "active = 1" returns None for inactive keys
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.auth.get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await require_tenant(mock_cred)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_tier_rate_limit_applied(self):
        """Different tiers should get different rate limits."""
        mock_cred = MagicMock(spec=HTTPAuthorizationCredentials)
        mock_cred.credentials = "pro-key"

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={
            "tenant_id": "tenant-123",
            "tier": "pro",
        })
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.auth.get_db", return_value=mock_db):
            with patch("saas.auth.limiter.check", return_value=True):
                with patch("saas.auth.settings.rate_for") as mock_rate:
                    mock_rate.return_value = 500
                    result = await require_tenant(mock_cred)

        assert result.rate_limit == 500
        mock_rate.assert_called_once_with("pro")
