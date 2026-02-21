"""Admin API — tenant & key management, protected by admin_secret."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Header, status

from ..config import settings
from ..db import get_db
from ..models import TenantCreate, TenantOut

router = APIRouter(prefix="/admin", tags=["admin"])


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
        "SELECT t.id, t.name, t.tier, t.created_at, "
        "(SELECT COUNT(*) FROM repos WHERE tenant_id = t.id) as repo_count, "
        "(SELECT COUNT(*) FROM api_keys WHERE tenant_id = t.id AND active = 1) as key_count "
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
async def update_tenant(tenant_id: str, tier: str | None = None, name: str | None = None):
    db = await get_db()
    cur = await db.execute("SELECT id FROM tenants WHERE id = ?", (tenant_id,))
    if not await cur.fetchone():
        raise HTTPException(404, "tenant not found")
    if tier:
        await db.execute("UPDATE tenants SET tier = ? WHERE id = ?", (tier, tenant_id))
    if name:
        await db.execute("UPDATE tenants SET name = ? WHERE id = ?", (name, tenant_id))
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
