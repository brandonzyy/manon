"""Tests for saas/quota.py — quota enforcement."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, status

from saas.quota import check_repo_quota
from saas.auth import TenantContext
from saas.config import SaasSettings


class TestCheckRepoQuota:
    """Tests for check_repo_quota function."""

    @pytest.mark.asyncio
    async def test_under_quota_passes(self):
        """Under quota should not raise."""
        ctx = TenantContext(tenant_id="tenant-1", tier="free")

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 2})
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.quota.get_db", return_value=mock_db):
            with patch.object(SaasSettings, "quota_repos", return_value=5):
                # Should not raise
                await check_repo_quota(ctx)

    @pytest.mark.asyncio
    async def test_at_quota_raises_403(self):
        """At quota limit should raise 403."""
        ctx = TenantContext(tenant_id="tenant-1", tier="free")

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 5})
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.quota.get_db", return_value=mock_db):
            with patch.object(SaasSettings, "quota_repos", return_value=5):
                with pytest.raises(HTTPException) as exc_info:
                    await check_repo_quota(ctx)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        # Free tier gets an upgrade pitch rather than the generic limit message.
        assert "repo" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_over_quota_raises_403(self):
        """Over quota should raise 403."""
        ctx = TenantContext(tenant_id="tenant-1", tier="free")

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 10})
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.quota.get_db", return_value=mock_db):
            with patch.object(SaasSettings, "quota_repos", return_value=5):
                with pytest.raises(HTTPException) as exc_info:
                    await check_repo_quota(ctx)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_tier_specific_limits(self):
        """Different tiers should have different limits."""
        # Pro tier should have higher limit
        ctx = TenantContext(tenant_id="tenant-1", tier="pro")

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 10})
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.quota.get_db", return_value=mock_db):
            with patch.object(SaasSettings, "quota_repos") as mock_quota:
                mock_quota.return_value = 50  # pro tier limit
                await check_repo_quota(ctx)

        mock_quota.assert_called_once_with("pro")

    @pytest.mark.asyncio
    async def test_zero_repos_passes(self):
        """Tenant with zero repos should pass."""
        ctx = TenantContext(tenant_id="tenant-1", tier="free")

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 0})
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.quota.get_db", return_value=mock_db):
            with patch.object(SaasSettings, "quota_repos", return_value=5):
                await check_repo_quota(ctx)  # Should not raise


class TestQuotaTierIntegration:
    """Tests for quota with different tiers."""

    @pytest.mark.asyncio
    async def test_free_tier_repo_limit(self):
        """Free tier should have low repo limit."""
        ctx = TenantContext(tenant_id="tenant-1", tier="free")

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 3})
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.quota.get_db", return_value=mock_db):
            with patch.object(SaasSettings, "quota_repos", return_value=3) as mock_quota:
                with pytest.raises(HTTPException):
                    await check_repo_quota(ctx)

        mock_quota.assert_called_once_with("free")

    @pytest.mark.asyncio
    async def test_pro_tier_higher_limits(self):
        """Pro tier should have higher limits than free."""
        ctx = TenantContext(tenant_id="tenant-1", tier="pro")

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 10})
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.quota.get_db", return_value=mock_db):
            with patch.object(SaasSettings, "quota_repos", return_value=50) as mock_quota:
                await check_repo_quota(ctx)

        mock_quota.assert_called_once_with("pro")

    @pytest.mark.asyncio
    async def test_enterprise_tier_unlimited(self):
        """Enterprise tier may have very high or unlimited quotas."""
        ctx = TenantContext(tenant_id="tenant-1", tier="enterprise")

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 100})
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        with patch("saas.quota.get_db", return_value=mock_db):
            with patch.object(SaasSettings, "quota_repos", return_value=1000) as mock_quota:
                await check_repo_quota(ctx)

        mock_quota.assert_called_once_with("enterprise")
