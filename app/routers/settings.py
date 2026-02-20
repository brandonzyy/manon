"""Settings API — runtime model configuration for Coach and Worker."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger("manon.settings")

router = APIRouter(tags=["settings"])

# ── Config file path ──
_CONFIG_FILE = Path(__file__).resolve().parent.parent / "settings.json"

# ── Built-in models ──
BUILTIN_MODELS = [
    {"id": "glm-4.7-fp8", "name": "GLM-4.7-FP8", "provider": "zhipu", "desc": "智谱免费编程模型（默认）"},
]

# ── Custom models (user-added via UI, persisted to settings.json) ──
_BUILTIN_CUSTOM: list[dict] = []

_DEFAULT_RUNTIME: dict[str, Any] = {
    "coach_model": "glm-4.7-fp8",
    "coach_model_fallback": "glm-4.7-fp8",
    "worker_model": "glm-4.7-fp8",
    "worker_model_fallback": "glm-4.7-fp8",
    "worker_compress_model": "glm-4.7-fp8",
}


def _load_config() -> dict:
    """Load persisted settings from settings.json."""
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Failed to load settings.json: %s", exc)
    return {}


def _save_config() -> None:
    """Persist current custom_models and runtime config to settings.json."""
    # Only save non-builtin custom models
    user_models = [m for m in _custom_models if not m.get("_builtin")]
    data = {"custom_models": user_models, "runtime": dict(_runtime)}
    try:
        _CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save settings.json: %s", exc)


# ── Initialize from config file ──
_saved = _load_config()
_custom_models: list[dict] = list(_BUILTIN_CUSTOM)
for m in _saved.get("custom_models", []):
    # Avoid duplicates by id
    if not any(existing["id"] == m["id"] for existing in _custom_models):
        _custom_models.append(m)

_runtime: dict[str, Any] = dict(_DEFAULT_RUNTIME)
_runtime.update(_saved.get("runtime", {}))


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


def get_runtime_config() -> dict[str, Any]:
    return dict(_runtime)


class ModelConfigUpdate(BaseModel):
    coach_model: str | None = None
    coach_model_fallback: str | None = None
    worker_model: str | None = None
    worker_model_fallback: str | None = None
    worker_compress_model: str | None = None


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
    if updated:
        _save_config()

    # Push model config to connected agents
    if any(f.startswith("worker_") for f in updated):
        from ..ws_hub import hub
        await hub.broadcast_to_agents({
            "type": "model-config",
            "model": _runtime["worker_model"],
            "modelFallback": _runtime["worker_model_fallback"],
            "compressModel": _runtime["worker_compress_model"],
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
    _save_config()
    log.info("Custom model added: %s (%s)", body.name, body.model_id)
    return {"status": "ok", "model": {"id": mid, "name": body.name, "provider": "custom", "desc": entry["desc"]}}


@router.delete("/settings/models/{model_id}")
async def delete_custom_model(model_id: str):
    for i, m in enumerate(_custom_models):
        if m["id"] == model_id:
            _custom_models.pop(i)
            _save_config()
            log.info("Custom model deleted: %s", model_id)
            return {"status": "ok"}
    return {"status": "not_found"}
