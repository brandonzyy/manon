"""Bearer token → TenantContext with subscription enforcement."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import get_db

_bearer = HTTPBearer()


@dataclass
class TenantContext:
    tenant_id: str
    tier: str


async def require_tenant(
    cred: HTTPAuthorizationCredentials = Depends(_bearer),
) -> TenantContext:
    token = cred.credentials
    db = await get_db()
    row = await db.execute(
        "SELECT k.tenant_id, t.tier, t.subscription_expires "
        "FROM api_keys k "
        "JOIN tenants t ON t.id = k.tenant_id "
        "WHERE k.key = ? AND k.active = 1",
        (token,),
    )
    row = await row.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or inactive api key")

    tenant_id, tier = row["tenant_id"], row["tier"]
    expires = row["subscription_expires"]

    # Check subscription/trial expiry for all tiers
    if expires:
        try:
            expires_dt = datetime.fromisoformat(expires).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now > expires_dt:
                if tier == "free":
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        "Free trial expired. Upgrade to Pro (¥399/mo) or Enterprise (¥999/mo) to continue.",
                    )
                else:
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        f"Subscription expired on {expires}. Please renew to continue.",
                    )
        except ValueError:
            pass  # malformed date — don't block

    return TenantContext(tenant_id=tenant_id, tier=tier)
