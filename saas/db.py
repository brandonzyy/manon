"""aiosqlite database helpers — schema init + connection pool."""
from __future__ import annotations

import aiosqlite

_db_path: str = ""
_conn: aiosqlite.Connection | None = None

SCHEMA = """\
CREATE TABLE IF NOT EXISTS tenants (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    tier       TEXT NOT NULL DEFAULT 'free',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
    key        TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL REFERENCES tenants(id),
    label      TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS repos (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(id),
    name         TEXT NOT NULL,
    git_url      TEXT NOT NULL DEFAULT '',
    branch       TEXT NOT NULL DEFAULT 'main',
    local_path   TEXT,
    index_status TEXT NOT NULL DEFAULT 'pending',
    index_stats  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id  TEXT NOT NULL,
    endpoint   TEXT NOT NULL,
    repo_id    TEXT,
    tokens     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS query_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       TEXT NOT NULL,
    repo_id         TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    query           TEXT NOT NULL,
    rounds          INTEGER NOT NULL DEFAULT 1,
    rounds_detail   TEXT,
    coverage        REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


_MIGRATIONS = [
    # Add source_type column for local AST sync repos
    "ALTER TABLE repos ADD COLUMN source_type TEXT NOT NULL DEFAULT ''",
    # query_log for embedding training data pipeline
    """CREATE TABLE IF NOT EXISTS query_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       TEXT NOT NULL,
        repo_id         TEXT NOT NULL,
        endpoint        TEXT NOT NULL,
        query           TEXT NOT NULL,
        rounds          INTEGER NOT NULL DEFAULT 1,
        rounds_detail   TEXT,
        coverage        REAL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # subscription billing
    "ALTER TABLE tenants ADD COLUMN subscription_expires TEXT",
    "ALTER TABLE tenants ADD COLUMN subscription_note TEXT",
]


async def _run_migrations(conn: aiosqlite.Connection) -> None:
    for sql in _MIGRATIONS:
        try:
            await conn.execute(sql)
        except Exception:
            pass  # column already exists
    await conn.commit()


async def init_db(db_path: str) -> None:
    global _db_path, _conn
    _db_path = db_path
    _conn = await aiosqlite.connect(db_path)
    _conn.row_factory = aiosqlite.Row
    await _conn.executescript(SCHEMA)
    await _conn.commit()
    await _run_migrations(_conn)


async def get_db() -> aiosqlite.Connection:
    assert _conn is not None, "call init_db first"
    return _conn


async def close_db() -> None:
    global _conn
    if _conn:
        await _conn.close()
        _conn = None
