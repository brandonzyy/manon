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
        # GLM-4.5 系列不传 thinking 时思考模式默认开启，planner 类调用会先生成
        # 数千 token 推理（单次 30-70s 且 content 可能为空）。注意 GLM-5.x
        # 强制开启思考，传 disabled 会直接报错——届时需移除此参数。
        json={"model": settings.llm_model, "max_tokens": max_tokens, "messages": messages,
              "thinking": {"type": "disabled"}},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    msg = data.get("choices", [{}])[0].get("message", {})
    # Reasoning models (e.g. glm-4.7-fp8) may put output in reasoning_content
    # when max_tokens is insufficient for both reasoning + content
    return msg.get("content") or msg.get("reasoning_content") or ""


def parse_json(text: str) -> dict | list:
    """Extract JSON from LLM output, stripping markdown fences and surrounding text."""
    # Strip markdown fences first
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fallback: find the first { ... } or [ ... ] block in the text
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = cleaned.find(start_char)
        if start == -1:
            continue
        end = cleaned.rfind(end_char)
        if end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No valid JSON found in LLM output: {text[:200]}")
