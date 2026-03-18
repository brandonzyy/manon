"""Tests for saas/rate_limit.py — sliding window rate limiter."""
import time
import pytest

from saas.rate_limit import SlidingWindowLimiter, limiter


class TestSlidingWindowLimiter:
    """Tests for SlidingWindowLimiter class."""

    def test_check_under_limit(self):
        """Requests under limit should return True."""
        rl = SlidingWindowLimiter(window=60)
        assert rl.check("tenant-1", limit=10) is True

    def test_check_at_limit(self):
        """Requests at limit should return False."""
        rl = SlidingWindowLimiter(window=60)
        for i in range(10):
            result = rl.check("tenant-1", limit=10)
        # The 10th request should be allowed (0-9), 11th rejected
        assert rl.check("tenant-1", limit=10) is False

    def test_multiple_tenants_separate_buckets(self):
        """Different tenants should have separate buckets."""
        rl = SlidingWindowLimiter(window=60)
        # Tenant 1 uses all quota
        for _ in range(5):
            rl.check("tenant-1", limit=5)
        assert rl.check("tenant-1", limit=5) is False

        # Tenant 2 should still be allowed
        assert rl.check("tenant-2", limit=5) is True

    def test_sliding_window_expiry(self):
        """Old entries should be pruned after window expires."""
        rl = SlidingWindowLimiter(window=1)  # 1 second window

        # Use all quota
        for _ in range(3):
            rl.check("tenant-1", limit=3)
        assert rl.check("tenant-1", limit=3) is False

        # Wait for window to expire
        time.sleep(1.1)

        # Should be allowed again
        assert rl.check("tenant-1", limit=3) is True

    def test_window_boundary(self):
        """Requests exactly at window edge should be handled correctly."""
        rl = SlidingWindowLimiter(window=1)

        # Make a request
        rl.check("tenant-1", limit=2)

        # Wait just under window
        time.sleep(0.9)

        # Should still count
        rl.check("tenant-1", limit=2)
        assert rl.check("tenant-1", limit=2) is False

    def test_pruning_on_check(self):
        """Old entries should be pruned during check."""
        rl = SlidingWindowLimiter(window=1)

        # Make requests
        rl.check("tenant-1", limit=10)
        time.sleep(1.1)

        # Old request should be pruned
        rl.check("tenant-1", limit=1)
        # Should only have 1 entry now (the new one)
        assert rl.check("tenant-1", limit=1) is False

    def test_empty_tenant_id(self):
        """Empty tenant_id should work (treated as a valid key)."""
        rl = SlidingWindowLimiter(window=60)
        assert rl.check("", limit=5) is True

    def test_limit_one(self):
        """Limit of 1 should allow exactly one request."""
        rl = SlidingWindowLimiter(window=60)
        assert rl.check("tenant-1", limit=1) is True
        assert rl.check("tenant-1", limit=1) is False

    def test_limit_zero_edge_case(self):
        """Limit of 0 should reject all requests."""
        rl = SlidingWindowLimiter(window=60)
        # First check with limit=0 should return False
        assert rl.check("tenant-1", limit=0) is False

    def test_large_limit(self):
        """Large limits should work correctly."""
        rl = SlidingWindowLimiter(window=60)
        for i in range(1000):
            result = rl.check("tenant-1", limit=1000)
            assert result is True
        assert rl.check("tenant-1", limit=1000) is False

    def test_bucket_isolation(self):
        """Each tenant's bucket should be isolated."""
        rl = SlidingWindowLimiter(window=60)

        # Use different amounts for different tenants
        for _ in range(3):
            rl.check("tenant-a", limit=10)
        for _ in range(7):
            rl.check("tenant-b", limit=10)

        # Both should have remaining quota
        assert rl.check("tenant-a", limit=10) is True   # tenant-a: 4th
        assert rl.check("tenant-b", limit=10) is True   # tenant-b: 8th
        assert rl.check("tenant-b", limit=10) is True   # tenant-b: 9th
        assert rl.check("tenant-b", limit=10) is True   # tenant-b: 10th (last allowed)
        assert rl.check("tenant-b", limit=10) is False  # tenant-b: 11th, exhausted

    def test_multiple_windows(self):
        """Different limiter instances should be independent."""
        rl1 = SlidingWindowLimiter(window=60)
        rl2 = SlidingWindowLimiter(window=60)

        for _ in range(5):
            rl1.check("tenant-1", limit=5)

        # rl2 should be independent
        assert rl2.check("tenant-1", limit=5) is True


class TestGlobalLimiter:
    """Tests for the global limiter instance."""

    def test_global_limiter_exists(self):
        """Global limiter should be available."""
        assert limiter is not None
        assert isinstance(limiter, SlidingWindowLimiter)

    def test_global_limiter_is_singleton(self):
        """Global limiter should be the same instance."""
        from saas.rate_limit import limiter as limiter2
        assert limiter is limiter2

    def test_global_limiter_functional(self):
        """Global limiter should be functional."""
        # Use unique tenant ID to avoid interference
        import uuid
        tenant = f"test-{uuid.uuid4()}"

        result = limiter.check(tenant, limit=100)
        assert result is True


class TestSlidingWindowEdgeCases:
    """Edge case tests for sliding window algorithm."""

    def test_exact_window_recalculation(self):
        """Test that window is recalculated exactly on each check."""
        rl = SlidingWindowLimiter(window=2)

        # Make request
        rl.check("t", limit=3)
        time.sleep(1)
        rl.check("t", limit=3)
        time.sleep(1.1)

        # First request should be pruned, second might still be in window
        # Depending on timing, we should have at least 1 slot
        assert rl.check("t", limit=3) is True

    def test_rapid_requests(self):
        """Rapid requests should be counted correctly."""
        rl = SlidingWindowLimiter(window=60)

        for i in range(10):
            rl.check("t", limit=10)

        assert rl.check("t", limit=10) is False

    def test_mixed_tenants_rapid(self):
        """Mixed tenant requests should not interfere."""
        rl = SlidingWindowLimiter(window=60)

        for i in range(5):
            rl.check("a", limit=5)
            rl.check("b", limit=10)

        assert rl.check("a", limit=5) is False
        assert rl.check("b", limit=10) is True
