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


def _active_model() -> str:
    """Return coach model from runtime config, falling back to static settings."""
    from ..routers.settings import get_runtime_config
    return get_runtime_config().get("coach_model") or get_settings().llm_model


def _active_fallback() -> str:
    from ..routers.settings import get_runtime_config
    return get_runtime_config().get("coach_model_fallback") or get_settings().llm_model_fallback


def _resolve_model(model: str) -> tuple[str, str, str]:
    """Return (model_name, api_url, api_key) — handles custom models."""
    from ..routers.settings import get_custom_model
    custom = get_custom_model(model)
    if custom:
        base = custom["api_url"].rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        return custom["model_id"], base + "/chat/completions", custom["api_key"]
    s = get_settings()
    return model, s.llm_api_url, s.llm_api_key


async def llm_chat(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> dict:
    """Low-level chat completion call. Returns {"content": str, "reasoning": str}."""
    model = model or _active_model()
    model_name, api_url, api_key = _resolve_model(model)
    client = _get_client()
    resp = await client.post(
        api_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model_name, "max_tokens": max_tokens, "messages": messages},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    msg = data.get("choices", [{}])[0].get("message", {})
    return {"content": msg.get("content", ""), "reasoning": msg.get("reasoning_content", "")}


async def llm_chat_stream(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: float = 120.0,
):
    """Streaming chat completion via openai SDK (proven SSE parser).

    Yields {"type": "reasoning"|"content", "delta": str}.
    """
    from openai import AsyncOpenAI

    model = model or _active_model()
    model_name, api_url, api_key = _resolve_model(model)
    # Derive base_url: strip /chat/completions if present, otherwise use as-is
    base_url = api_url.rsplit("/chat/completions", 1)[0]
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    try:
        stream = await client.chat.completions.create(
            model=model_name, messages=messages, max_tokens=max_tokens, stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # reasoning_content is ZhipuAI-specific → lives in model_extra
            rc = (delta.model_extra or {}).get("reasoning_content", "")
            cc = delta.content or ""
            if rc:
                yield {"type": "reasoning", "delta": rc}
            if cc:
                yield {"type": "content", "delta": cc}
    finally:
        await client.close()


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
    model = model or _active_model()
    msgs = messages or [
        {"role": "system", "content": system_prompt or ""},
        {"role": "user", "content": user_prompt or ""},
    ]
    try:
        result = await llm_chat(msgs, model=model, max_tokens=max_tokens, timeout=timeout)
        return result["content"]
    except Exception as exc:
        fallback = _active_fallback()
        if fallback and fallback != model:
            log.warning("%s failed (%s), falling back to %s", model, exc, fallback)
            result = await llm_chat(msgs, model=fallback, max_tokens=max_tokens, timeout=timeout)
            return result["content"]
        raise


async def call_glm5_full(
    system_prompt: str | None,
    user_prompt: str | None,
    *,
    messages: list[dict] | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> dict:
    """Like call_glm5 but returns {"content": str, "reasoning": str}."""
    model = model or _active_model()
    msgs = messages or [
        {"role": "system", "content": system_prompt or ""},
        {"role": "user", "content": user_prompt or ""},
    ]
    try:
        return await llm_chat(msgs, model=model, max_tokens=max_tokens, timeout=timeout)
    except Exception as exc:
        fallback = _active_fallback()
        if fallback and fallback != model:
            log.warning("%s failed (%s), falling back to %s", model, exc, fallback)
            return await llm_chat(msgs, model=fallback, max_tokens=max_tokens, timeout=timeout)
        raise


def parse_json_from_llm(text: str) -> dict | list:
    """Extract JSON from LLM output, stripping markdown fences."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```", "", cleaned).strip()
    return json.loads(cleaned)
