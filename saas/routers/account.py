"""GET /api/v1/account — tenant info, usage stats, quota status."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import TenantContext, require_tenant
from ..config import settings
from ..db import get_db

router = APIRouter(prefix="/api/v1", tags=["account"])


@router.get("/account")
async def get_account(ctx: TenantContext = Depends(require_tenant)):
    db = await get_db()

    # repo count
    cur = await db.execute(
        "SELECT COUNT(*) as cnt FROM repos WHERE tenant_id = ?", (ctx.tenant_id,),
    )
    repo_count = (await cur.fetchone())["cnt"]

    # today's deep-query count
    cur = await db.execute(
        "SELECT COUNT(*) as cnt FROM usage_log "
        "WHERE tenant_id = ? AND endpoint = 'query.deep_query' "
        "AND created_at >= datetime('now', '-1 day')",
        (ctx.tenant_id,),
    )
    deep_query_today = (await cur.fetchone())["cnt"]

    # total API calls (last 30 days)
    cur = await db.execute(
        "SELECT COUNT(*) as cnt FROM usage_log "
        "WHERE tenant_id = ? AND created_at >= datetime('now', '-30 day')",
        (ctx.tenant_id,),
    )
    total_calls_30d = (await cur.fetchone())["cnt"]

    return {
        "tenant_id": ctx.tenant_id,
        "tier": ctx.tier,
        "rate_limit": ctx.rate_limit,
        "quotas": {
            "repos": {"used": repo_count, "limit": settings.quota_repos(ctx.tier)},
            "deep_query_daily": {"used": deep_query_today, "limit": settings.quota_deep_query(ctx.tier)},
        },
        "usage_30d": total_calls_30d,
    }
