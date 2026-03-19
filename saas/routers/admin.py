"""Admin API — tenant & key management, protected by admin_secret."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status

from ..config import settings
from ..db import get_db
from ..models import TenantCreate, TenantOut

router = APIRouter(prefix="/admin", tags=["admin"])


def _merge_impact_endpoints(by_endpoint_raw: list) -> list:
    """Merge query.impact and query.impact_local into a single entry."""
    by_endpoint = []
    impact_count = 0
    for item in by_endpoint_raw:
        if item["endpoint"] in ("query.impact", "query.impact_local"):
            impact_count += item["count"]
        else:
            by_endpoint.append(item)
    if impact_count > 0:
        by_endpoint.append({"endpoint": "query.impact", "count": impact_count})
    by_endpoint.sort(key=lambda x: x["count"], reverse=True)
    return by_endpoint


# ── Auth dependency ───────────────────────────────────
async def require_admin(x_admin_secret: str = Header(...)):
    if not settings.admin_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "admin_secret not configured")
    if x_admin_secret != settings.admin_secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid admin secret")


# ── Tenants ───────────────────────────────────────────
@router.get("/tenants", dependencies=[Depends(require_admin)])
async def list_tenants():
    db = await get_db()
    cur = await db.execute(
        "SELECT t.id, t.name, t.tier, t.created_at, t.subscription_expires, "
        "(SELECT COUNT(*) FROM repos WHERE tenant_id = t.id) as repo_count, "
        "(SELECT COUNT(*) FROM api_keys WHERE tenant_id = t.id AND active = 1) as key_count, "
        "(SELECT COUNT(*) FROM usage_log WHERE tenant_id = t.id) as total_calls, "
        "(SELECT key FROM api_keys WHERE tenant_id = t.id AND active = 1 ORDER BY created_at LIMIT 1) as primary_key "
        "FROM tenants t ORDER BY t.created_at DESC"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/tenants", status_code=201, dependencies=[Depends(require_admin)])
async def create_tenant(body: TenantCreate):
    db = await get_db()
    tenant_id = uuid.uuid4().hex[:8]
    api_key = f"msk_{uuid.uuid4().hex}"
    await db.execute(
        "INSERT INTO tenants (id, name, tier) VALUES (?, ?, ?)",
        (tenant_id, body.name, body.tier),
    )
    await db.execute(
        "INSERT INTO api_keys (key, tenant_id, label) VALUES (?, ?, ?)",
        (api_key, tenant_id, "default"),
    )
    await db.commit()
    return TenantOut(id=tenant_id, name=body.name, tier=body.tier, api_key=api_key)


@router.patch("/tenants/{tenant_id}", dependencies=[Depends(require_admin)])
async def update_tenant(
    tenant_id: str,
    tier: str | None = None,
    name: str | None = None,
    subscription_months: int | None = None,
    subscription_expires: str | None = None,
):
    db = await get_db()
    cur = await db.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
    tenant = await cur.fetchone()
    if not tenant:
        raise HTTPException(404, "tenant not found")
    if tier:
        await db.execute("UPDATE tenants SET tier = ? WHERE id = ?", (tier, tenant_id))
    if name:
        await db.execute("UPDATE tenants SET name = ? WHERE id = ?", (name, tenant_id))
    if subscription_months is not None and subscription_months > 0:
        # extend from current expiry or now
        existing = tenant["subscription_expires"]
        if existing:
            try:
                base = datetime.fromisoformat(existing)
            except ValueError:
                base = datetime.now(timezone.utc)
        else:
            base = datetime.now(timezone.utc)
        # if already expired, extend from now instead
        if base < datetime.now(timezone.utc):
            base = datetime.now(timezone.utc)
        new_month = base.month + subscription_months
        new_year = base.year + (new_month - 1) // 12
        new_month = (new_month - 1) % 12 + 1
        new_day = min(base.day, [31,29 if new_year%4==0 and (new_year%100!=0 or new_year%400==0) else 28,31,30,31,30,31,31,30,31,30,31][new_month-1])
        new_expires = base.replace(year=new_year, month=new_month, day=new_day)
        await db.execute(
            "UPDATE tenants SET subscription_expires = ? WHERE id = ?",
            (new_expires.isoformat(), tenant_id),
        )
    elif subscription_expires is not None:
        # direct set — empty string clears it
        val = subscription_expires if subscription_expires else None
        await db.execute(
            "UPDATE tenants SET subscription_expires = ? WHERE id = ?",
            (val, tenant_id),
        )
    await db.commit()
    cur = await db.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
    return dict(await cur.fetchone())


# ── Keys ──────────────────────────────────────────────
@router.get("/tenants/{tenant_id}/keys", dependencies=[Depends(require_admin)])
async def list_keys(tenant_id: str):
    db = await get_db()
    cur = await db.execute(
        "SELECT key, label, active, created_at FROM api_keys WHERE tenant_id = ? ORDER BY created_at DESC",
        (tenant_id,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/tenants/{tenant_id}/keys", status_code=201, dependencies=[Depends(require_admin)])
async def create_key(tenant_id: str, label: str = "admin-created"):
    db = await get_db()
    cur = await db.execute("SELECT id FROM tenants WHERE id = ?", (tenant_id,))
    if not await cur.fetchone():
        raise HTTPException(404, "tenant not found")
    api_key = f"msk_{uuid.uuid4().hex}"
    await db.execute(
        "INSERT INTO api_keys (key, tenant_id, label) VALUES (?, ?, ?)",
        (api_key, tenant_id, label),
    )
    await db.commit()
    return {"key": api_key, "tenant_id": tenant_id, "label": label}


@router.delete("/tenants/{tenant_id}/keys/{key}", dependencies=[Depends(require_admin)])
async def revoke_key(tenant_id: str, key: str):
    db = await get_db()
    await db.execute(
        "UPDATE api_keys SET active = 0 WHERE key = ? AND tenant_id = ?",
        (key, tenant_id),
    )
    await db.commit()
    return {"ok": True, "key": key, "status": "revoked"}


# ── Usage Stats ───────────────────────────────────────
# Merge query.impact and query.impact_local for unified statistics
@router.get("/usage/overview", dependencies=[Depends(require_admin)])
async def get_usage_overview(days: int = Query(30, ge=1, le=365)):
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(tokens),0) as tok "
        "FROM usage_log WHERE created_at >= datetime('now', ?)",
        (f"-{days} days",),
    )
    row = await cur.fetchone()
    cur2 = await db.execute(
        "SELECT DATE(created_at) as date, COUNT(*) as cnt FROM usage_log "
        "WHERE created_at >= datetime('now', ?) "
        "GROUP BY DATE(created_at) ORDER BY date",
        (f"-{days} days",),
    )
    by_date = [{"date": r["date"], "count": r["cnt"]} for r in await cur2.fetchall()]
    cur3 = await db.execute(
        "SELECT endpoint, COUNT(*) as cnt FROM usage_log "
        "WHERE created_at >= datetime('now', ?) "
        "GROUP BY endpoint ORDER BY cnt DESC",
        (f"-{days} days",),
    )
    by_endpoint_raw = [{"endpoint": r["endpoint"], "count": r["cnt"]} for r in await cur3.fetchall()]
    by_endpoint = _merge_impact_endpoints(by_endpoint_raw)

    return {
        "period_days": days,
        "total_calls": row["cnt"],
        "total_tokens": row["tok"],
        "by_date": by_date,
        "by_endpoint": by_endpoint,
    }


@router.get("/tenants/{tenant_id}/usage", dependencies=[Depends(require_admin)])
async def get_tenant_usage(tenant_id: str, days: int = Query(30, ge=1, le=365)):
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(tokens),0) as tok "
        "FROM usage_log WHERE tenant_id = ? AND created_at >= datetime('now', ?)",
        (tenant_id, f"-{days} days"),
    )
    row = await cur.fetchone()
    cur2 = await db.execute(
        "SELECT DATE(created_at) as date, COUNT(*) as cnt FROM usage_log "
        "WHERE tenant_id = ? AND created_at >= datetime('now', ?) "
        "GROUP BY DATE(created_at) ORDER BY date",
        (tenant_id, f"-{days} days"),
    )
    by_date = [{"date": r["date"], "count": r["cnt"]} for r in await cur2.fetchall()]
    cur3 = await db.execute(
        "SELECT endpoint, COUNT(*) as cnt FROM usage_log "
        "WHERE tenant_id = ? AND created_at >= datetime('now', ?) "
        "GROUP BY endpoint ORDER BY cnt DESC",
        (tenant_id, f"-{days} days"),
    )
    by_endpoint_raw = [{"endpoint": r["endpoint"], "count": r["cnt"]} for r in await cur3.fetchall()]

    by_endpoint = _merge_impact_endpoints(by_endpoint_raw)

    return {
        "tenant_id": tenant_id,
        "period_days": days,
        "total_calls": row["cnt"],
        "total_tokens": row["tok"],
        "by_date": by_date,
        "by_endpoint": by_endpoint,
    }
