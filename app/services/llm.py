"""LLM service — async API calls via httpx, default glm-4.7-fp8."""

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


def _resolve_model(model: str) -> tuple[str, str, str, str]:
    """Return (model_name, api_url, api_key, api_format) — handles custom models."""
    from ..routers.settings import get_custom_model
    custom = get_custom_model(model)
    if custom:
        fmt = custom.get("api_format", "openai")
        base = custom["api_url"].rstrip("/")
        if fmt == "anthropic":
            if "/messages" not in base:
                if not base.endswith("/v1"):
                    base += "/v1"
                base += "/messages"
            return custom["model_id"], base, custom["api_key"], "anthropic"
        else:
            if "/chat/completions" not in base:
                if not base.endswith("/v1"):
                    base += "/v1"
                base += "/chat/completions"
            return custom["model_id"], base, custom["api_key"], "openai"
    s = get_settings()
    return model, s.llm_api_url, s.llm_api_key, "openai"


def _split_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Extract system prompt from messages list for Anthropic API format."""
    system = ""
    filtered = []
    for m in messages:
        if m["role"] == "system":
            system = m.get("content", "")
        else:
            filtered.append(m)
    return system, filtered


async def _anthropic_chat(
    client: httpx.AsyncClient,
    model: str, url: str, api_key: str,
    messages: list[dict], max_tokens: int, timeout: float,
) -> dict:
    """Non-streaming Anthropic Messages API call."""
    system, msgs = _split_anthropic_messages(messages)
    body: dict = {"model": model, "max_tokens": max_tokens, "messages": msgs}
    if system:
        body["system"] = system
    resp = await client.post(
        url,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    content = ""
    reasoning = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")
        elif block.get("type") == "thinking":
            reasoning += block.get("thinking", "")
    return {"content": content, "reasoning": reasoning}


async def _anthropic_chat_stream(
    model: str, url: str, api_key: str,
    messages: list[dict], max_tokens: int, timeout: float,
):
    """Streaming Anthropic Messages API — yields {"type": ..., "delta": ...}."""
    system, msgs = _split_anthropic_messages(messages)
    body: dict = {"model": model, "max_tokens": max_tokens, "messages": msgs, "stream": True}
    if system:
        body["system"] = system
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        async with client.stream(
            "POST", url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=body,
        ) as resp:
            resp.raise_for_status()
            buf = ""
            async for raw in resp.aiter_text():
                buf += raw
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        return
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("type") == "content_block_delta":
                        delta = evt.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield {"type": "content", "delta": delta.get("text", "")}
                        elif delta.get("type") == "thinking_delta":
                            yield {"type": "reasoning", "delta": delta.get("thinking", "")}


async def llm_chat(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> dict:
    """Low-level chat completion call. Returns {"content": str, "reasoning": str}."""
    model = model or _active_model()
    model_name, api_url, api_key, api_format = _resolve_model(model)
    client = _get_client()

    if api_format == "anthropic":
        return await _anthropic_chat(client, model_name, api_url, api_key, messages, max_tokens, timeout)

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
    """Streaming chat completion.

    Yields {"type": "reasoning"|"content", "delta": str}.
    """
    model = model or _active_model()
    model_name, api_url, api_key, api_format = _resolve_model(model)

    if api_format == "anthropic":
        async for chunk in _anthropic_chat_stream(model_name, api_url, api_key, messages, max_tokens, timeout):
            yield chunk
        return

    from openai import AsyncOpenAI
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
