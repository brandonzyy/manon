"""Tests for saas/auth.py — authentication and tenant context."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from saas.auth import TenantContext, require_tenant
from saas.config import SaasSettings


class TestTenantContext:
    """Tests for TenantContext dataclass."""

    def test_create_tenant_context(self):
        """Should create context with all fields."""
        ctx = TenantContext(
            tenant_id="tenant-123",
            tier="pro",
        )
        assert ctx.tenant_id == "tenant-123"
        assert ctx.tier == "pro"

    def test_tenant_context_default_values(self):
        """Should have required fields only."""
        ctx = TenantContext(tenant_id="t1", tier="free")
        assert ctx.tenant_id == "t1"
        assert ctx.tier == "free"


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
            "subscription_expires": None,
        })
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.auth.get_db", return_value=mock_db):
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

