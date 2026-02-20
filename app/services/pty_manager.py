"""PTY session manager — spawn interactive CLI in a pseudo-terminal, proxy I/O."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Callable, Awaitable

log = logging.getLogger("manon.pty")

# Detect platform
_IS_WIN = os.name == "nt"


class PtySession:
    """Wraps a single PTY process with async read/write."""

    def __init__(self, proc, loop: asyncio.AbstractEventLoop):
        self._proc = proc
        self._loop = loop
        self._alive = True
        self._read_task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        if _IS_WIN:
            return self._alive and self._proc.isalive()
        return self._alive

    def write(self, data: str) -> None:
        if not self.alive:
            return
        if _IS_WIN:
            self._proc.write(data)
        else:
            os.write(self._proc, data.encode())

    def resize(self, cols: int, rows: int) -> None:
        if _IS_WIN and self.alive:
            self._proc.setwinsize(rows, cols)

    async def read_loop(self, on_output: Callable[[str], Awaitable[None]]) -> None:
        """Continuously read PTY output and call on_output. Runs until process exits."""
        try:
            while self.alive:
                data = await self._loop.run_in_executor(None, self._blocking_read)
                if data is None:
                    break
                await on_output(data)
        except Exception as exc:
            log.debug("PTY read loop ended: %s", exc)
        finally:
            self._alive = False

    def _blocking_read(self) -> str | None:
        try:
            if _IS_WIN:
                # pywinpty read with timeout
                data = self._proc.read(4096)
                return data if data else None
            else:
                data = os.read(self._proc, 4096)
                return data.decode(errors="replace") if data else None
        except Exception:
            return None

    def kill(self) -> None:
        self._alive = False
        try:
            if _IS_WIN:
                self._proc.close(force=True)
        except Exception:
            pass


class PtyManager:
    """Manages PTY sessions per dev connection."""

    def __init__(self):
        self._sessions: dict[str, PtySession] = {}

    async def spawn(
        self,
        dev_id: str,
        cli: str,
        *,
        cwd: str | None = None,
        cols: int = 120,
        rows: int = 40,
        on_output: Callable[[str], Awaitable[None]] | None = None,
    ) -> PtySession:
        """Spawn a CLI in a PTY. Returns the session."""
        # Kill existing session if any
        await self.kill(dev_id)

        bin_path = shutil.which(cli)
        if not bin_path:
            raise FileNotFoundError(f"CLI not found: {cli}")

        loop = asyncio.get_event_loop()

        if _IS_WIN:
            from winpty import PtyProcess
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            proc = PtyProcess.spawn(
                bin_path,
                cwd=cwd or os.getcwd(),
                dimensions=(rows, cols),
                env=env,
            )
        else:
            raise NotImplementedError("Unix PTY not yet implemented")

        session = PtySession(proc, loop)
        self._sessions[dev_id] = session
        log.info("PTY spawned for %s: %s (pid=%s)", dev_id, cli, getattr(proc, 'pid', '?'))

        # Start read loop in background
        if on_output:
            session._read_task = asyncio.create_task(session.read_loop(on_output))

        return session

    def get(self, dev_id: str) -> PtySession | None:
        s = self._sessions.get(dev_id)
        if s and not s.alive:
            self._sessions.pop(dev_id, None)
            return None
        return s

    async def kill(self, dev_id: str) -> None:
        session = self._sessions.pop(dev_id, None)
        if session:
            session.kill()
            if session._read_task:
                session._read_task.cancel()
            log.info("PTY killed for %s", dev_id)

    async def shutdown(self) -> None:
        for dev_id in list(self._sessions):
            await self.kill(dev_id)


# Singleton
pty_mgr = PtyManager()
