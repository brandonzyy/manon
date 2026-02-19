"""Manon Gateway — FastAPI app + lifespan + WebSocket endpoints."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import get_settings
from .db import init_db
from .ws_hub import hub
from .routers import health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("manon")


async def _on_upstream_message(msg: dict) -> None:
    """Handle messages from API Server (task done/failed) → forward to pipeline."""
    from .coach.pipeline import handle_upstream_message
    await handle_upstream_message(msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db(settings.db_path)
    log.info("DB initialized at %s", settings.db_path)
    await hub.connect_upstream(settings.api_server_ws, on_msg=_on_upstream_message)
    log.info("Upstream connection started → %s", settings.api_server_ws)
    yield
    await hub.shutdown()
    log.info("Manon Gateway shut down")


app = FastAPI(title="Manon Gateway", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)

# Late imports to avoid circular deps at module level
def _include_routers():
    from .routers import projects, query, indexing
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(query.router, prefix="/api/v1")
    app.include_router(indexing.router, prefix="/api/v1")

_include_routers()


@app.websocket("/ws/dev")
async def ws_dev(ws: WebSocket):
    dev_id = await hub.accept_dev(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue
            msg["_dev_id"] = dev_id
            from .coach.pipeline import handle_dev_message
            await handle_dev_message(dev_id, msg)
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_dev(dev_id)
