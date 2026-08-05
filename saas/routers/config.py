"""GET /api/v1/config — return LLM / embedding configuration."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import TenantContext, require_tenant
from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["config"])


@router.get("/config")
async def get_config(ctx: TenantContext = Depends(require_tenant)):
    return {
        "llm_model": settings.llm_model,
        "llm_api_url": settings.llm_api_url,
        "tenant_id": ctx.tenant_id,
        "tier": ctx.tier,
    }
