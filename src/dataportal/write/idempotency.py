"""Idempotency key storage and deduplication."""
from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite


IDEMPOTENCY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _dataportal_idempotency (
    key TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    response_status INTEGER NOT NULL,
    response_body TEXT NOT NULL
)
"""


async def ensure_idempotency_table(conn: aiosqlite.Connection):
    await conn.execute(IDEMPOTENCY_TABLE_SQL)
    await conn.commit()


async def get_cached_response(
    conn: aiosqlite.Connection, key: str, window_seconds: int = 3600
) -> dict | None:
    """Check if an idempotency key was already processed within the window."""
    cursor = await conn.execute(
        "SELECT response_status, response_body, created_at FROM _dataportal_idempotency WHERE key = ?",
        (key,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    status, body, created_at = row
    if time.time() - created_at > window_seconds:
        await conn.execute("DELETE FROM _dataportal_idempotency WHERE key = ?", (key,))
        await conn.commit()
        return None

    return {"status": status, "body": json.loads(body)}


async def store_response(
    conn: aiosqlite.Connection, key: str, status: int, body: dict
):
    """Store the result of an idempotent operation."""
    await conn.execute(
        """INSERT OR REPLACE INTO _dataportal_idempotency (key, created_at, response_status, response_body)
           VALUES (?, ?, ?, ?)""",
        (key, time.time(), status, json.dumps(body, ensure_ascii=False)),
    )
    await conn.commit()


async def cleanup_expired(conn: aiosqlite.Connection, window_seconds: int = 3600):
    """Remove expired idempotency records."""
    cutoff = time.time() - window_seconds
    await conn.execute(
        "DELETE FROM _dataportal_idempotency WHERE created_at < ?", (cutoff,)
    )
    await conn.commit()
