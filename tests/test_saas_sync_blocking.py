"""Regression: a slow sync-ast batch must not block the event loop.

sync-ast used to run graph load/process/save directly on the event loop.
A large batch froze the whole service — /health stopped answering, the
client saw 502s and 30s timeouts, and the server watchdog killed the
process mid-write (2026-09-08 outage). The batch now runs in a worker
thread; this test pins that contract by measuring /health latency while
a batch is in flight.
"""
import asyncio
import time

import httpx
import pytest
import pytest_asyncio

from saas.config import settings
from saas.db import close_db, get_db, init_db
from saas.main import app
from saas.routers import indexing


@pytest_asyncio.fixture
async def seeded_db(tmp_path, monkeypatch):
    """Temp DB + index dir with one tenant/api key/repo. Returns auth context."""
    await init_db(str(tmp_path / "saas.db"))
    monkeypatch.setattr(settings, "index_dir", str(tmp_path / "idx"))
    db = await get_db()
    await db.execute("INSERT INTO tenants (id, name, tier) VALUES ('t1', 'test', 'enterprise')")
    await db.execute("INSERT INTO api_keys (key, tenant_id) VALUES ('sk_test_1', 't1')")
    await db.execute("INSERT INTO repos (id, tenant_id, name) VALUES ('r1', 't1', 'demo')")
    await db.commit()
    yield {"api_key": "sk_test_1", "repo_id": "r1", "tenant_id": "t1"}
    await close_db()


def _patch_pipeline(monkeypatch, batch_fn) -> None:
    """Replace the thread-offloaded graph work; keep embed/persist cheap no-ops."""

    async def no_embed(*_args, **_kwargs):
        pass

    monkeypatch.setattr(indexing, "_load_and_process_batch", batch_fn)
    monkeypatch.setattr(indexing, "_embed_and_index_vectors", no_embed)
    monkeypatch.setattr(indexing, "_persist_kg_state", lambda *_args, **_kwargs: None)


@pytest.mark.asyncio
async def test_health_stays_responsive_during_slow_batch(seeded_db, monkeypatch):
    BATCH_SECONDS = 1.5

    def slow_batch(_repo_id, _kg_path, _body):
        time.sleep(BATCH_SECONDS)
        return indexing._BatchResult(
            graph=None, vec_index=None, all_chunks={}, meta={},
            new_hashes={}, entities=[], chunks=[], stats={"files_synced": 0},
        )

    _patch_pipeline(monkeypatch, slow_batch)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        sync_task = asyncio.create_task(client.post(
            f"/api/v1/repos/{seeded_db['repo_id']}/sync-ast",
            json={"files": [], "deleted_files": [], "is_final_batch": True},
            headers={"Authorization": f"Bearer {seeded_db['api_key']}"},
        ))
        try:
            await asyncio.sleep(0.3)  # sync request is now inside the slow batch
            t0 = time.monotonic()
            health = await client.get("/health")
            latency = time.monotonic() - t0
        finally:
            resp = await sync_task

    assert resp.status_code == 200
    assert health.status_code == 200
    # Inline (pre-fix) processing would hold the loop for the whole batch
    # and this latency would approach BATCH_SECONDS - 0.3.
    assert latency < 0.5, f"/health took {latency:.2f}s while a batch was syncing"


@pytest.mark.asyncio
async def test_sync_failure_marks_repo_error(seeded_db, monkeypatch):
    """A crashing batch must surface as index_status=error, not hang the request."""

    def broken_batch(_repo_id, _kg_path, _body):
        raise RuntimeError("boom")

    _patch_pipeline(monkeypatch, broken_batch)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/repos/{seeded_db['repo_id']}/sync-ast",
            json={"files": [], "deleted_files": [], "is_final_batch": True},
            headers={"Authorization": f"Bearer {seeded_db['api_key']}"},
        )
        assert resp.status_code == 200  # sync-ast reports errors via repo status

        db = await get_db()
        cur = await db.execute("SELECT index_status FROM repos WHERE id = ?", (seeded_db["repo_id"],))
        row = await cur.fetchone()
        assert row["index_status"] == "error"
