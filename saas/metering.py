"""Usage metering — record every API call to usage_log + query_log."""
from __future__ import annotations

import json
import logging

from .db import get_db

log = logging.getLogger("saas.metering")


async def record_usage(
    tenant_id: str,
    endpoint: str,
    repo_id: str | None = None,
    tokens: int = 0,
) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO usage_log (tenant_id, endpoint, repo_id, tokens) VALUES (?, ?, ?, ?)",
        (tenant_id, endpoint, repo_id, tokens),
    )
    await db.commit()


async def record_query(
    tenant_id: str,
    repo_id: str,
    endpoint: str,
    query: str,
    rounds: int = 1,
    rounds_detail: list[dict] | None = None,
    coverage: float | None = None,
) -> None:
    """Record query details for embedding training data pipeline.

    rounds_detail format:
    [
      {"round": 0, "query": "原始问题", "entities": ["e1"], "chunks": ["c1","c2"], "covered": false},
      {"round": 1, "query": "补充查询词", "entities": ["e3"], "chunks": ["c3"], "covered": true}
    ]

    Fire-and-forget: caller should wrap in asyncio.create_task().
    """
    try:
        db = await get_db()
        await db.execute(
            "INSERT INTO query_log "
            "(tenant_id, repo_id, endpoint, query, rounds, rounds_detail, coverage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                tenant_id, repo_id, endpoint, query, rounds,
                json.dumps(rounds_detail, ensure_ascii=False) if rounds_detail else None,
                coverage,
            ),
        )
        await db.commit()
    except Exception as e:
        log.warning("Failed to record query log: %s", e)
