"""Settings API — runtime model configuration for Coach and Agent."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger("manon.settings")

router = APIRouter(tags=["settings"])

# ── Built-in models ──
BUILTIN_MODELS = [
    {"id": "GLM-5", "name": "GLM-5", "provider": "zhipu", "desc": "智谱旗舰模型"},
    {"id": "GLM-4.7", "name": "GLM-4.7", "provider": "zhipu", "desc": "智谱编程模型"},
    {"id": "GLM-4-Plus", "name": "GLM-4-Plus", "provider": "zhipu", "desc": "智谱增强模型"},
    {"id": "deepseek-chat", "name": "DeepSeek V3", "provider": "deepseek", "desc": "DeepSeek 对话模型"},
    {"id": "deepseek-reasoner", "name": "DeepSeek R1", "provider": "deepseek", "desc": "DeepSeek 推理模型"},
]

# ── Custom models (user-added, OpenAI-compatible) ──
# Each: {"id": str, "name": str, "model_id": str, "api_url": str, "api_key": str, "desc": str, "provider": "custom"}
_custom_models: list[dict] = []


def get_all_models() -> list[dict]:
    """Return built-in + custom models for dropdown display."""
    result = list(BUILTIN_MODELS)
    for m in _custom_models:
        result.append({
            "id": m["id"], "name": m["name"],
            "provider": "custom", "desc": m.get("desc", "自定义模型"),
        })
    return result


def get_custom_model(model_id: str) -> dict | None:
    """Look up a custom model by its selection id."""
    for m in _custom_models:
        if m["id"] == model_id:
            return m
    return None


# ── Runtime config (in-memory, survives until restart) ──
_runtime: dict[str, Any] = {
    "coach_model": "GLM-5",
    "coach_model_fallback": "GLM-4.7",
    "agent_model": "GLM-5",
    "agent_model_fallback": "GLM-4.7",
    "agent_compress_model": "GLM-5",
}


def get_runtime_config() -> dict[str, Any]:
    return dict(_runtime)


class ModelConfigUpdate(BaseModel):
    coach_model: str | None = None
    coach_model_fallback: str | None = None
    agent_model: str | None = None
    agent_model_fallback: str | None = None
    agent_compress_model: str | None = None


class CustomModelAdd(BaseModel):
    name: str
    model_id: str  # the model name sent to the API (e.g. "gpt-4o")
    api_url: str   # base URL (e.g. "https://api.openai.com/v1")
    api_key: str
    api_format: str = "openai"  # "openai" or "anthropic"


class CustomModelDelete(BaseModel):
    id: str


@router.get("/settings")
async def get_settings():
    return {
        "models": get_all_models(),
        "custom_models": _custom_models,
        "config": get_runtime_config(),
    }


@router.put("/settings")
async def update_settings(body: ModelConfigUpdate):
    updated = []
    for field in body.model_fields_set:
        val = getattr(body, field)
        if val is not None and val != _runtime.get(field):
            _runtime[field] = val
            updated.append(field)
            log.info("Config updated: %s = %s", field, val)

    # Push model config to connected agents
    if any(f.startswith("agent_") for f in updated):
        from ..ws_hub import hub
        await hub.broadcast_to_agents({
            "type": "model-config",
            "model": _runtime["agent_model"],
            "modelFallback": _runtime["agent_model_fallback"],
            "compressModel": _runtime["agent_compress_model"],
        })
        log.info("Pushed model-config to %d agents", len(hub._agents))

    return {"status": "ok", "updated": updated, "config": get_runtime_config()}


@router.post("/settings/models")
async def add_custom_model(body: CustomModelAdd):
    mid = f"custom-{uuid.uuid4().hex[:8]}"
    fmt = body.api_format if body.api_format in ("openai", "anthropic") else "openai"
    entry = {
        "id": mid,
        "name": body.name,
        "model_id": body.model_id,
        "api_url": body.api_url.rstrip("/"),
        "api_key": body.api_key,
        "api_format": fmt,
        "desc": f"{body.model_id} @ {body.api_url[:40]}",
        "provider": "custom",
    }
    _custom_models.append(entry)
    log.info("Custom model added: %s (%s)", body.name, body.model_id)
    return {"status": "ok", "model": {"id": mid, "name": body.name, "provider": "custom", "desc": entry["desc"]}}


@router.delete("/settings/models/{model_id}")
async def delete_custom_model(model_id: str):
    for i, m in enumerate(_custom_models):
        if m["id"] == model_id:
            _custom_models.pop(i)
            log.info("Custom model deleted: %s", model_id)
            return {"status": "ok"}
    return {"status": "not_found"}
