"""In-memory sliding-window rate limiter (per tenant, 60s window)."""
from __future__ import annotations

import time
from collections import defaultdict


class SlidingWindowLimiter:
    def __init__(self, window: int = 60):
        self._window = window
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, tenant_id: str, limit: int) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        bucket = self._hits[tenant_id]
        # prune expired
        self._hits[tenant_id] = bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


limiter = SlidingWindowLimiter()
