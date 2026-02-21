"""GET /api/v1/account — tenant info, usage stats, quota status.
   POST/DELETE /api/v1/account/keys — user self-service key management."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

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


# ── User key management ──────────────────────────────
@router.get("/account/keys")
async def list_my_keys(ctx: TenantContext = Depends(require_tenant)):
    db = await get_db()
    cur = await db.execute(
        "SELECT key, label, active, created_at FROM api_keys "
        "WHERE tenant_id = ? ORDER BY created_at DESC",
        (ctx.tenant_id,),
    )
    rows = await cur.fetchall()
    # mask keys: show first 8 chars + last 4
    return [
        {
            "key": r["key"][:8] + "..." + r["key"][-4:],
            "key_full": r["key"],
            "label": r["label"],
            "active": bool(r["active"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.post("/account/keys", status_code=201)
async def create_my_key(label: str = "user-created", ctx: TenantContext = Depends(require_tenant)):
    db = await get_db()
    # limit: max 5 active keys per tenant
    cur = await db.execute(
        "SELECT COUNT(*) as cnt FROM api_keys WHERE tenant_id = ? AND active = 1",
        (ctx.tenant_id,),
    )
    count = (await cur.fetchone())["cnt"]
    if count >= 5:
        raise HTTPException(403, "max 5 active keys per tenant")
    api_key = f"msk_{uuid.uuid4().hex}"
    await db.execute(
        "INSERT INTO api_keys (key, tenant_id, label) VALUES (?, ?, ?)",
        (api_key, ctx.tenant_id, label),
    )
    await db.commit()
    return {"key": api_key, "label": label}


@router.delete("/account/keys/{key}")
async def revoke_my_key(key: str, ctx: TenantContext = Depends(require_tenant)):
    db = await get_db()
    # only revoke own keys
    cur = await db.execute(
        "SELECT key FROM api_keys WHERE key = ? AND tenant_id = ? AND active = 1",
        (key, ctx.tenant_id),
    )
    if not await cur.fetchone():
        raise HTTPException(404, "key not found or already revoked")
    # prevent revoking last active key
    cur = await db.execute(
        "SELECT COUNT(*) as cnt FROM api_keys WHERE tenant_id = ? AND active = 1",
        (ctx.tenant_id,),
    )
    if (await cur.fetchone())["cnt"] <= 1:
        raise HTTPException(403, "cannot revoke last active key")
    await db.execute("UPDATE api_keys SET active = 0 WHERE key = ?", (key,))
    await db.commit()
    return {"ok": True, "key": key, "status": "revoked"}
