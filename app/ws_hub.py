"""WebSocket hub — manages developer connections + upstream API Server link."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("manon.ws")


class WSHub:
    """Manages two kinds of connections:
    1. Developer clients (FastAPI WebSocket at /ws/dev)
    2. Upstream API Server coach channel (websockets client to /ws/coach)
    """

    def __init__(self) -> None:
        # dev_id → WebSocket
        self._devs: dict[str, WebSocket] = {}
        self._upstream_ws: Any | None = None
        self._upstream_task: asyncio.Task | None = None
        self._upstream_url: str = ""
        # callback for incoming upstream messages
        self._on_upstream_msg: Any | None = None
        self._counter = 0

    # ---- developer connections ----

    def _next_id(self) -> str:
        self._counter += 1
        return f"dev-{self._counter}"

    async def accept_dev(self, ws: WebSocket) -> str:
        await ws.accept()
        dev_id = self._next_id()
        self._devs[dev_id] = ws
        log.info("Developer %s connected (%d total)", dev_id, len(self._devs))
        return dev_id

    def remove_dev(self, dev_id: str) -> None:
        self._devs.pop(dev_id, None)
        log.info("Developer %s disconnected (%d remain)", dev_id, len(self._devs))
    async def send_to_dev(self, dev_id: str, data: dict) -> None:
        ws = self._devs.get(dev_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.remove_dev(dev_id)

    async def broadcast_to_devs(self, data: dict) -> None:
        dead: list[str] = []
        for did, ws in self._devs.items():
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(did)
        for did in dead:
            self.remove_dev(did)

    # ---- upstream API Server connection ----

    async def connect_upstream(self, url: str, on_msg=None) -> None:
        self._upstream_url = url
        self._on_upstream_msg = on_msg
        self._upstream_task = asyncio.create_task(self._upstream_loop())

    async def _upstream_loop(self) -> None:
        backoff = 5
        while True:
            try:
                async with websockets.connect(self._upstream_url) as ws:
                    self._upstream_ws = ws
                    log.info("Connected to upstream %s", self._upstream_url)
                    backoff = 5
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            if self._on_upstream_msg:
                                await self._on_upstream_msg(msg)
                        except json.JSONDecodeError:
                            pass
            except Exception as exc:
                log.warning("Upstream disconnected: %s, retry in %ds", exc, backoff)
                self._upstream_ws = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    async def send_upstream(self, data: dict) -> None:
        if self._upstream_ws:
            try:
                await self._upstream_ws.send(json.dumps(data))
            except Exception:
                log.warning("Failed to send upstream message")

    async def shutdown(self) -> None:
        if self._upstream_task:
            self._upstream_task.cancel()
        if self._upstream_ws:
            await self._upstream_ws.close()


# Singleton
hub = WSHub()
