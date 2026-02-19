"""Settings API — runtime model configuration for Coach and Agent."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger("manon.settings")

router = APIRouter(tags=["settings"])

# ── Available models ──
AVAILABLE_MODELS = [
    {"id": "GLM-5", "name": "GLM-5", "provider": "zhipu", "desc": "智谱旗舰模型"},
    {"id": "GLM-4.7", "name": "GLM-4.7", "provider": "zhipu", "desc": "智谱编程模型"},
    {"id": "GLM-4-Plus", "name": "GLM-4-Plus", "provider": "zhipu", "desc": "智谱增强模型"},
    {"id": "deepseek-chat", "name": "DeepSeek V3", "provider": "deepseek", "desc": "DeepSeek 对话模型"},
    {"id": "deepseek-reasoner", "name": "DeepSeek R1", "provider": "deepseek", "desc": "DeepSeek 推理模型"},
]

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


@router.get("/settings")
async def get_settings():
    return {"models": AVAILABLE_MODELS, "config": get_runtime_config()}


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
