"""GET /health, /tunnel-url, /version — liveness probe, tunnel URL registry, version check."""
from pathlib import Path
from fastapi import APIRouter, Body

from ..config import settings

router = APIRouter(tags=["health"])

LATEST_VERSION = "0.1.0"

_TUNNEL_URL_FILE = Path("/tmp/manon_tunnel_url.txt")

def _load_tunnel_url() -> str:
    try:
        return _TUNNEL_URL_FILE.read_text().strip()
    except Exception:
        return ""

_tunnel_url: str = _load_tunnel_url()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "llm_model": settings.llm_model,
        "embedding_url": settings.embedding_url,
    }


@router.get("/version")
async def get_version():
    return {"version": LATEST_VERSION}


@router.get("/tunnel-url")
async def get_tunnel_url():
    return {"url": _tunnel_url}


@router.post("/tunnel-url")
async def set_tunnel_url(url: str = Body(..., embed=True)):
    global _tunnel_url
    _tunnel_url = url
    _TUNNEL_URL_FILE.write_text(url)
    return {"ok": True, "url": _tunnel_url}
