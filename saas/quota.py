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
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Repo limit reached ({limit} for {ctx.tier} tier). Upgrade to add more.",
        )


async def check_deep_query_quota(ctx: TenantContext) -> None:
    """Raise 403 if tenant has exceeded daily deep-query limit."""
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) as cnt FROM usage_log "
        "WHERE tenant_id = ? AND endpoint = 'query.deep_query' "
        "AND created_at >= datetime('now', '-1 day')",
        (ctx.tenant_id,),
    )
    row = await cur.fetchone()
    limit = settings.quota_deep_query(ctx.tier)
    if row["cnt"] >= limit:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Daily deep-query limit reached ({limit} for {ctx.tier} tier). Upgrade for unlimited.",
        )
