"""LLM service — async GLM-5 API calls via httpx, mirroring coach-llm.js patterns."""

from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import get_settings

log = logging.getLogger("manon.llm")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    return _client


async def llm_chat(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> str:
    """Low-level chat completion call."""
    s = get_settings()
    model = model or s.llm_model
    client = _get_client()
    resp = await client.post(
        s.llm_api_url,
        headers={"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json"},
        json={"model": model, "max_tokens": max_tokens, "messages": messages},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


async def call_glm5(
    system_prompt: str | None,
    user_prompt: str | None,
    *,
    messages: list[dict] | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> str:
    """High-level wrapper with fallback, mirroring coach-llm.js callGLM5."""
    s = get_settings()
    model = model or s.llm_model
    msgs = messages or [
        {"role": "system", "content": system_prompt or ""},
        {"role": "user", "content": user_prompt or ""},
    ]
    try:
        return await llm_chat(msgs, model=model, max_tokens=max_tokens, timeout=timeout)
    except Exception as exc:
        fallback = s.llm_model_fallback
        if fallback and fallback != model:
            log.warning("%s failed (%s), falling back to %s", model, exc, fallback)
            return await llm_chat(msgs, model=fallback, max_tokens=max_tokens, timeout=timeout)
        raise


def parse_json_from_llm(text: str) -> dict | list:
    """Extract JSON from LLM output, stripping markdown fences."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```", "", cleaned).strip()
    return json.loads(cleaned)
