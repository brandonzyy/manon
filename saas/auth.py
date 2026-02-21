"""Bearer token → TenantContext with rate-limit enforcement."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import get_db
from .rate_limit import limiter
from .config import settings

_bearer = HTTPBearer()


@dataclass
class TenantContext:
    tenant_id: str
    tier: str
    rate_limit: int


async def require_tenant(
    cred: HTTPAuthorizationCredentials = Depends(_bearer),
) -> TenantContext:
    token = cred.credentials
    db = await get_db()
    row = await db.execute(
        "SELECT k.tenant_id, t.tier FROM api_keys k "
        "JOIN tenants t ON t.id = k.tenant_id "
        "WHERE k.key = ? AND k.active = 1",
        (token,),
    )
    row = await row.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or inactive api key")

    tenant_id, tier = row["tenant_id"], row["tier"]
    limit = settings.rate_for(tier)

    if not limiter.check(tenant_id, limit):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")

    return TenantContext(tenant_id=tenant_id, tier=tier, rate_limit=limit)
