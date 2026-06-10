"""Audit logging for write operations."""
from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite


AUDIT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _dataportal_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    operation TEXT NOT NULL,
    db_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_pk TEXT,
    before_data TEXT,
    after_data TEXT,
    user_token TEXT,
    idempotency_key TEXT
)
"""


async def ensure_audit_table(conn: aiosqlite.Connection):
    await conn.execute(AUDIT_TABLE_SQL)
    await conn.commit()


async def log_mutation(
    conn: aiosqlite.Connection,
    operation: str,
    db_name: str,
    table_name: str,
    row_pk: str | None = None,
    before_data: dict | None = None,
    after_data: dict | None = None,
    user_token: str | None = None,
    idempotency_key: str | None = None,
):
    await conn.execute(
        """INSERT INTO _dataportal_audit
           (timestamp, operation, db_name, table_name, row_pk, before_data, after_data, user_token, idempotency_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            time.time(),
            operation,
            db_name,
            table_name,
            row_pk,
            json.dumps(before_data, ensure_ascii=False) if before_data else None,
            json.dumps(after_data, ensure_ascii=False) if after_data else None,
            user_token,
            idempotency_key,
        ),
    )
