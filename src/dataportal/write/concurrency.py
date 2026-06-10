"""Optimistic concurrency control via version column."""
from __future__ import annotations

from typing import Any

import aiosqlite


class ConcurrencyConflictError(Exception):
    def __init__(self, message: str = "Version conflict - row was modified by another request"):
        self.message = message
        super().__init__(message)


async def get_row_with_version(
    conn: aiosqlite.Connection, table_name: str, pk_column: str, pk_value: Any
) -> tuple[dict | None, int | None]:
    """Fetch a row and its _version value (if column exists)."""
    cursor = await conn.execute(
        f'SELECT * FROM "{table_name}" WHERE "{pk_column}" = ?', (pk_value,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None, None

    columns = [d[0] for d in cursor.description]
    row_dict = dict(zip(columns, row))
    version = row_dict.get("_version")
    return row_dict, version


async def check_version(
    conn: aiosqlite.Connection,
    table_name: str,
    pk_column: str,
    pk_value: Any,
    expected_version: int,
) -> bool:
    """Check if current version matches expected. Raises ConcurrencyConflictError if not."""
    cursor = await conn.execute(
        f'SELECT _version FROM "{table_name}" WHERE "{pk_column}" = ?', (pk_value,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise ConcurrencyConflictError("Row not found")

    current_version = row[0]
    if current_version != expected_version:
        raise ConcurrencyConflictError(
            f"Expected version {expected_version}, but current is {current_version}"
        )
    return True


async def has_version_column(conn: aiosqlite.Connection, table_name: str) -> bool:
    """Check if the table has a _version column."""
    cursor = await conn.execute(f'PRAGMA table_info("{table_name}")')
    columns = await cursor.fetchall()
    return any(c[1] == "_version" for c in columns)
