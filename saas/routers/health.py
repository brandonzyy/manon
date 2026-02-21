"""GET /health — liveness probe with config summary."""
from fastapi import APIRouter

from ..config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "llm_model": settings.llm_model,
        "embedding_url": settings.embedding_url,
    }
