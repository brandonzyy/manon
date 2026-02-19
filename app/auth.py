"""Bearer-token authentication for Manon API."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .db import db_pool

_bearer = HTTPBearer(auto_error=False)


async def _valid_key(key: str) -> bool:
    """Check key against settings list + DB api_keys table."""
    if key in get_settings().api_keys:
        return True
    async with db_pool() as db:
        row = await db.execute_fetchone(
            "SELECT 1 FROM api_keys WHERE key = ? AND active = 1", (key,)
        )
        return row is not None


async def require_api_key(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if cred is None or not await _valid_key(cred.credentials):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key")
    return cred.credentials
