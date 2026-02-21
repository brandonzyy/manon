"""Usage reporting — GET /api/v1/usage."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import TenantContext, require_tenant
from ..db import get_db
from ..models import UsageSummary

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


@router.get("")
async def get_usage(
    days: int = Query(30, ge=1, le=365),
    ctx: TenantContext = Depends(require_tenant),
):
    db = await get_db()

    cur = await db.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(tokens),0) as tok "
        "FROM usage_log WHERE tenant_id = ? AND created_at >= datetime('now', ?)",
        (ctx.tenant_id, f"-{days} days"),
    )
    row = await cur.fetchone()

    cur2 = await db.execute(
        "SELECT endpoint, COUNT(*) as cnt FROM usage_log "
        "WHERE tenant_id = ? AND created_at >= datetime('now', ?) "
        "GROUP BY endpoint",
        (ctx.tenant_id, f"-{days} days"),
    )
    by_endpoint = {r["endpoint"]: r["cnt"] for r in await cur2.fetchall()}

    return UsageSummary(
        tenant_id=ctx.tenant_id,
        period_days=days,
        total_calls=row["cnt"],
        total_tokens=row["tok"],
        by_endpoint=by_endpoint,
    )
