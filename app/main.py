"""Manon Gateway — FastAPI app + lifespan + WebSocket endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .ws_hub import hub
from .routers import health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("manon")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db(settings.db_path)
    log.info("DB initialized at %s", settings.db_path)
    # Configure MatrixoneGraph pool
    from matrixone_graph import MatrixoneGraph
    MatrixoneGraph.configure(
        embedding_url=settings.embedding_url,
        data_dir=settings.index_dir,
    )
    log.info("MatrixoneGraph: embedding_url=%s, data_dir=%s", settings.embedding_url, settings.index_dir)
    # Reconcile projects DB with actual index files on disk
    from .routers.projects import reconcile_projects
    await reconcile_projects()
    # Start monitor broadcast loop
    monitor_task = asyncio.create_task(_monitor_broadcast_loop())
    log.info("Manon Gateway ready (no upstream — direct agent management)")
    # Auto-open browser
    webbrowser.open(f"http://localhost:{settings.port}")
    yield
    monitor_task.cancel()
    # Shutdown worker pool
    from .worker import worker_pool
    await worker_pool.shutdown()
    await MatrixoneGraph.shutdown_all()
    await hub.shutdown()
    log.info("Manon Gateway shut down")


app = FastAPI(title="Manon Gateway", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _include_routers():
    from .routers import projects, query, indexing, settings
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(query.router, prefix="/api/v1")
    app.include_router(indexing.router, prefix="/api/v1")
    app.include_router(settings.router, prefix="/api/v1")

_include_routers()


# ── WebSocket: Developer ──

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
            try:
                await handle_dev_message(dev_id, msg)
            except Exception as exc:
                log.exception("handle_dev_message error for %s: %s", dev_id, exc)
                await ws.send_json({"type": "error", "message": f"内部错误: {exc}"})
            await hub.broadcast_to_monitors({**msg, "_dir": "in"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_dev(dev_id)


# ── WebSocket: Agent (auto-fix.js connects here) ──

@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    agent_id = await hub.accept_agent(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg["_agent_id"] = agent_id
            await _handle_agent_message(agent_id, msg)
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_agent(agent_id)


async def _handle_agent_message(agent_id: str, msg: dict) -> None:
    """Route messages from auto-fix agent to coach pipeline."""
    t = msg.get("type", "")
    # Forward to monitors
    await hub.broadcast_to_monitors({**msg, "_dir": "agent-in"})

    if t in ("feature-task-done", "feature-task-failed"):
        from .coach.pipeline import handle_agent_result
        await handle_agent_result(msg)
    elif t == "agent-status":
        log.info("Agent %s status: %s", agent_id, msg.get("status"))
    elif t == "agent-log":
        pass  # silently forward to monitors (already done above)
    elif t == "graph-effectiveness":
        log.info("Graph effectiveness: hit_rate=%s%%", msg.get("hitRate"))


# ── WebSocket: Monitor ──

@app.websocket("/ws/monitor")
async def ws_monitor(ws: WebSocket):
    await hub.accept_monitor(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_monitor(ws)


async def _monitor_broadcast_loop():
    """Periodically push state snapshot to monitor clients."""
    while True:
        await asyncio.sleep(3)
        from .coach.pipeline import _sessions
        sessions = []
        for dev_id, state in _sessions.items():
            sessions.append({
                "devId": dev_id,
                "featureId": state.feature_id,
                "projectId": state.project_id,
                "status": state.status.value if hasattr(state.status, 'value') else str(state.status),
                "tasks": [{"id": t.get("id"), "status": t.get("status")} for t in state.tasks],
            })
        # Collect indexing status from in-memory tasks
        from .routers.projects import _index_tasks
        indexing = {pid: {"status": info["status"]} for pid, info in _index_tasks.items()
                    if info["status"] == "indexing"}
        # Worker pool active count
        from .worker import worker_pool
        await hub.broadcast_to_monitors({
            "type": "monitor-state",
            "agentCount": len(hub._agents),
            "workerCount": worker_pool.active_count,
            "devCount": len(hub._devs),
            "sessions": sessions,
            "indexing": indexing,
        })


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, log_level="info")
