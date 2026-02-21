"""WorkerPool — semaphore-gated async worker management."""

from __future__ import annotations

import asyncio
import logging

from .runner import execute_task

log = logging.getLogger("manon.worker.pool")

MAX_WORKERS = 5


class WorkerPool:
    """Manages concurrent worker slots with asyncio.Semaphore."""

    def __init__(self, max_workers: int = MAX_WORKERS) -> None:
        self._sem = asyncio.Semaphore(max_workers)
        self._active: list[asyncio.Task] = []
        self._max = max_workers

    @property
    def active_count(self) -> int:
        self._active = [t for t in self._active if not t.done()]
        return len(self._active)

    async def submit(self, task_config: dict) -> dict:
        """Submit a task to the pool. Blocks until a slot is available, then runs to completion.

        Returns {"type": "feature-task-done/failed", ...}.
        """
        await self._sem.acquire()
        log.info("Worker slot acquired (%d/%d active)", self.active_count + 1, self._max)
        try:
            result = await execute_task(task_config)
            return result
        except Exception as exc:
            log.error("Worker task failed with exception: %s", exc)
            return {
                "type": "feature-task-failed",
                "featureId": task_config.get("featureId", ""),
                "taskId": task_config.get("taskId", ""),
                "reason": str(exc),
            }
        finally:
            self._sem.release()
            log.info("Worker slot released (%d/%d active)", self.active_count, self._max)

    async def shutdown(self) -> None:
        """Cancel all active workers."""
        for t in self._active:
            if not t.done():
                t.cancel()
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)
        self._active.clear()
        log.info("WorkerPool shut down")
