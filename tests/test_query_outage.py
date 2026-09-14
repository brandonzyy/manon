"""Regression: embedding outage must fail loud, and not brick whole repos.

2026-09-14 outage: embedding API hit quota exhaustion (429 code 1113).
Incremental syncs failed -> index_status=error -> every query endpoint
returned 400 "repo not indexed yet"; searches on done-status repos raised
raw 500s from the unhandled HTTPStatusError. Pinned behavior:
- the gate serves the last-good graph for error-status repos (sync only
  persists after embedding succeeds, so the on-disk graph is consistent);
- embedding failures surface as explicit 503 with the underlying reason —
  never silently degraded results, never a bare 500.
"""
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException

from matrixone_graph.pipeline import query as pipeline_query
from matrixone_graph.store import CodeGraph, Entity
from saas.auth import TenantContext
from saas.config import settings
from saas.db import close_db, get_db, init_db
from saas.services import query as services_query
from saas.services.query import require_indexed_repo


@pytest_asyncio.fixture
async def seeded_db(tmp_path, monkeypatch):
    await init_db(str(tmp_path / "saas.db"))
    monkeypatch.setattr(settings, "index_dir", str(tmp_path / "idx"))
    db = await get_db()
    await db.execute("INSERT INTO tenants (id, name, tier) VALUES ('t1', 'test', 'enterprise')")
    await db.execute("INSERT INTO api_keys (key, tenant_id) VALUES ('sk_test_1', 't1')")
    await db.commit()
    yield tmp_path
    await close_db()


async def _add_repo(repo_id: str, index_status: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO repos (id, tenant_id, name, index_status) VALUES (?, 't1', ?, ?)",
        (repo_id, repo_id, index_status),
    )
    await db.commit()


def _kg_with_graph(idx_root: Path, tenant: str, name: str) -> None:
    kg = idx_root / tenant / name / "kg"
    kg.mkdir(parents=True, exist_ok=True)
    (kg / "graph.json").write_text("{}", encoding="utf-8")


def _quota_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://embedding.test/embeddings")
    resp = httpx.Response(429, content=b'{"error":{"code":"1113","message":"balance"}}', request=req)
    return httpx.HTTPStatusError("429 quota", request=req, response=resp)


class _BrokenEmbedder:
    async def embed_single(self, _text: str) -> list[float]:
        raise _quota_error()


class _BrokenMG:
    async def query(self, *_args, **_kwargs):
        raise _quota_error()


class TestRequireIndexedRepo:
    @pytest.mark.asyncio
    async def test_error_status_with_graph_passes_gate(self, seeded_db):
        """Failed incremental sync leaves the last good graph — still queryable."""
        await _add_repo("r1", "error")
        _kg_with_graph(seeded_db / "idx", "t1", "r1")
        row = await require_indexed_repo("r1", "t1")
        assert row["index_status"] == "error"

    @pytest.mark.asyncio
    async def test_error_status_without_graph_rejected(self, seeded_db):
        """First-sync failure has no graph on disk — reject."""
        await _add_repo("r1", "error")
        assert not (seeded_db / "idx" / "t1" / "r1" / "kg" / "graph.json").exists()
        with pytest.raises(HTTPException) as exc_info:
            await require_indexed_repo("r1", "t1")
        assert "not indexed" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_pending_status_rejected(self, seeded_db):
        await _add_repo("r1", "pending")
        _kg_with_graph(seeded_db / "idx", "t1", "r1")  # even with a file, pending never synced
        with pytest.raises(HTTPException) as exc_info:
            await require_indexed_repo("r1", "t1")
        assert "not indexed" in str(exc_info.value.detail)


class TestLoudOutage:
    @pytest.mark.asyncio
    async def test_pipeline_query_propagates_embedding_error(self, tmp_path):
        """No silent fallback: the embedding error escapes pipeline.query."""
        kg = tmp_path / "kg"
        kg.mkdir()
        graph = CodeGraph()
        graph.add_entity(Entity(id="pkg.mod.my_func", kind="function", name="my_func"))
        graph.save(kg / "graph.json")

        with pytest.raises(httpx.HTTPStatusError):
            await pipeline_query(tmp_path, "my_func", _BrokenEmbedder(),
                                 top_k=3, depth=0, kg_path=kg)

    @pytest.mark.asyncio
    async def test_search_repo_returns_explicit_503(self, seeded_db, monkeypatch):
        """Embedding outage surfaces as 503 with the reason, not a bare 500."""
        await _add_repo("r1", "done")
        _kg_with_graph(seeded_db / "idx", "t1", "r1")
        monkeypatch.setattr(services_query, "get_graph", lambda *_args, **_kwargs: _BrokenMG())

        async def _noop(*_args, **_kwargs):
            pass

        monkeypatch.setattr(services_query, "record_usage", _noop)
        monkeypatch.setattr(services_query, "record_query", _noop)

        ctx = TenantContext(tenant_id="t1", tier="enterprise")
        with pytest.raises(HTTPException) as exc_info:
            await services_query.search_repo("r1", "anything", top_k=3, depth=1, ctx=ctx)

        assert exc_info.value.status_code == 503
        assert "embedding service unavailable" in exc_info.value.detail
        assert "429" in exc_info.value.detail
