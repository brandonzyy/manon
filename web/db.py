"""SQLite via aiosqlite — schema init + thin helpers."""

from __future__ import annotations

import contextlib
from pathlib import Path

import aiosqlite

_db_path: str = ""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key         TEXT PRIMARY KEY,
    label       TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL DEFAULT '',
    feature_id  TEXT,
    messages    TEXT NOT NULL DEFAULT '[]',
    state       TEXT NOT NULL DEFAULT 'idle',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db(path: str) -> None:
    global _db_path
    _db_path = path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(_SCHEMA)
        # Seed default dev API key
        await conn.execute(
            "INSERT OR IGNORE INTO api_keys (key, label) VALUES (?, ?)",
            ("dev", "Local development key"),
        )
        await conn.commit()


@contextlib.asynccontextmanager
async def db_pool():
    """Yield an aiosqlite connection with execute_fetchone helper."""
    async with aiosqlite.connect(_db_path) as conn:
        conn.row_factory = aiosqlite.Row

        async def _fetchone(sql, params=()):
            cursor = await conn.execute(sql, params)
            return await cursor.fetchone()

        conn.execute_fetchone = _fetchone
        yield conn
