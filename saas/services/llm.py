"""Minimal LLM helper — OpenAI-compatible chat completion."""
from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import settings

log = logging.getLogger("saas.llm")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    return _client


async def close_llm_client() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


async def llm_chat(
    messages: list[dict],
    *,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> str:
    """Chat completion → returns content string."""
    client = _get_client()
    resp = await client.post(
        settings.llm_api_url,
        headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"},
        json={"model": settings.llm_model, "max_tokens": max_tokens, "messages": messages},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


def parse_json(text: str) -> dict | list:
    """Extract JSON from LLM output, stripping markdown fences."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```", "", cleaned).strip()
    return json.loads(cleaned)
