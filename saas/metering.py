"""Usage metering — record every API call to usage_log."""
from __future__ import annotations

from .db import get_db


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
