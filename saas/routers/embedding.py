"""Embedding gateway — proxy to cloud embedding service (GLM / OpenAI-compatible)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import TenantContext, require_tenant
from ..metering import record_usage
from ..config import settings

router = APIRouter(prefix="/api/v1/embedding", tags=["embedding"])

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=60)
    return _client


class EmbedRequest(BaseModel):
    inputs: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    count: int


@router.post("", response_model=EmbedResponse)
async def embed_texts(
    body: EmbedRequest,
    ctx: TenantContext = Depends(require_tenant),
):
    """Proxy embedding request to cloud embedding service."""
    if not body.inputs:
        raise HTTPException(400, "inputs must not be empty")
    if len(body.inputs) > 128:
        raise HTTPException(400, "max 128 texts per request")

    client = _get_client()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.embedding_api_key:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"

    try:
        resp = await client.post(
            f"{settings.embedding_url.rstrip('/')}/embeddings",
            headers=headers,
            json={"model": settings.embedding_model, "input": body.inputs},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"embedding service error: {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(502, f"embedding service unreachable: {e}")

    data = resp.json()
    items = sorted(data["data"], key=lambda x: x["index"])
    vectors = [item["embedding"] for item in items]

    await record_usage(ctx.tenant_id, "embedding", tokens=len(body.inputs))
    return EmbedResponse(embeddings=vectors, count=len(vectors))


async def close_embedding_client():
    global _client
    if _client:
        await _client.aclose()
        _client = None
