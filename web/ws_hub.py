"""WebSocket hub — manages developer, agent, and monitor connections."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("manon.ws")


class WSHub:
    """Manages three kinds of connections:
    1. Developer clients (/ws/dev)
    2. Auto-fix agents (/ws/agent)
    3. Monitor dashboards (/ws/monitor)
    """

    def __init__(self) -> None:
        self._devs: dict[str, WebSocket] = {}
        self._agents: dict[str, WebSocket] = {}
        self._monitors: list[WebSocket] = []
        self._dev_counter = 0
        self._agent_counter = 0
        # Callback for agent messages (set by main.py)
        self._on_agent_msg: Any | None = None

    # ── developer connections ──

    def _next_dev_id(self) -> str:
        self._dev_counter += 1
        return f"dev-{self._dev_counter}"

    async def accept_dev(self, ws: WebSocket) -> str:
        await ws.accept()
        dev_id = self._next_dev_id()
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

    # ── agent connections ──

    def _next_agent_id(self) -> str:
        self._agent_counter += 1
        return f"agent-{self._agent_counter}"

    async def accept_agent(self, ws: WebSocket) -> str:
        await ws.accept()
        agent_id = self._next_agent_id()
        self._agents[agent_id] = ws
        log.info("Agent %s connected (%d total)", agent_id, len(self._agents))
        return agent_id

    def remove_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        log.info("Agent %s disconnected (%d remain)", agent_id, len(self._agents))

    async def send_to_agent(self, agent_id: str, data: dict) -> None:
        ws = self._agents.get(agent_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.remove_agent(agent_id)

    async def broadcast_to_agents(self, data: dict) -> None:
        dead: list[str] = []
        for aid, ws in self._agents.items():
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(aid)
        for aid in dead:
            self.remove_agent(aid)

    @property
    def has_agents(self) -> bool:
        return len(self._agents) > 0

    # ── monitor connections ──

    async def accept_monitor(self, ws: WebSocket) -> None:
        await ws.accept()
        self._monitors.append(ws)
        log.info("Monitor connected (%d total)", len(self._monitors))

    def remove_monitor(self, ws: WebSocket) -> None:
        if ws in self._monitors:
            self._monitors.remove(ws)
        log.info("Monitor disconnected (%d remain)", len(self._monitors))

    async def broadcast_to_monitors(self, data: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self._monitors:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_monitor(ws)

    async def shutdown(self) -> None:
        pass


# Singleton
hub = WSHub()
