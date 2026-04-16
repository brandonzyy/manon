"""Quota enforcement — check tier-based limits before operations."""
from __future__ import annotations

from fastapi import HTTPException, status

from .auth import TenantContext
from .config import settings
from .db import get_db


async def check_repo_quota(ctx: TenantContext) -> None:
    """Raise 403 if tenant has reached their repo limit."""
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) as cnt FROM repos WHERE tenant_id = ?", (ctx.tenant_id,),
    )
    row = await cur.fetchone()
    limit = settings.quota_repos(ctx.tier)
    if row["cnt"] >= limit:
        tier_msg = {
            "free": "Free trial allows 1 repo. Upgrade to Pro (¥399/mo) for 5 repos.",
            "pro": "Pro plan allows 5 repos. Upgrade to Enterprise (¥999/mo) for unlimited.",
        }
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            tier_msg.get(ctx.tier, f"Repo limit reached ({limit})."),
        )
