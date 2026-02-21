"""GET /health, /tunnel-url — liveness probe and tunnel URL registry."""
from fastapi import APIRouter, Body

from ..config import settings

router = APIRouter(tags=["health"])

_tunnel_url: str = ""


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "llm_model": settings.llm_model,
        "embedding_url": settings.embedding_url,
    }


@router.get("/tunnel-url")
async def get_tunnel_url():
    return {"url": _tunnel_url}


@router.post("/tunnel-url")
async def set_tunnel_url(url: str = Body(..., embed=True)):
    global _tunnel_url
    _tunnel_url = url
    return {"ok": True, "url": _tunnel_url}
