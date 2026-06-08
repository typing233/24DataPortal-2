"""Database registry and connection management."""
import asyncio
import sqlite3
import hashlib
import time
from pathlib import Path
from typing import Any

import aiosqlite


class DatabaseInfo:
    def __init__(self, name: str, path: str, source_type: str):
        self.name = name
        self.path = path
        self.source_type = source_type
        self.tables: list[dict] = []
        self.views: list[dict] = []
        self.size_bytes: int = 0
        self.last_refreshed: float = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "source_type": self.source_type,
            "tables": self.tables,
            "views": self.views,
            "size_bytes": self.size_bytes,
            "last_refreshed": self.last_refreshed,
        }


class DatabaseRegistry:
    def __init__(self):
        self._databases: dict[str, DatabaseInfo] = {}
        self._connections: dict[str, aiosqlite.Connection] = {}
        self._lock = asyncio.Lock()

    @property
    def databases(self) -> dict[str, DatabaseInfo]:
        return self._databases

    async def register(self, name: str, path: str, source_type: str = "sqlite"):
        info = DatabaseInfo(name=name, path=path, source_type=source_type)
        p = Path(path)
        if p.exists():
            info.size_bytes = p.stat().st_size
        self._databases[name] = info
        await self.refresh_schema(name)

    async def get_connection(self, db_name: str) -> aiosqlite.Connection:
        if db_name not in self._connections or not self._connections[db_name]._running:
            path = self._databases[db_name].path
            conn = await aiosqlite.connect(path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            self._connections[db_name] = conn
        return self._connections[db_name]

    async def refresh_schema(self, db_name: str):
        conn = await self.get_connection(db_name)
        info = self._databases[db_name]

        cursor = await conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"
        )
        rows = await cursor.fetchall()

        info.tables = []
        info.views = []
        for row in rows:
            name, obj_type = row[0], row[1]
            if name.startswith("sqlite_"):
                continue
            count_cursor = await conn.execute(f'SELECT COUNT(*) FROM "{name}"')
            count_row = await count_cursor.fetchone()
            count = count_row[0] if count_row else 0

            col_cursor = await conn.execute(f'PRAGMA table_info("{name}")')
            columns = await col_cursor.fetchall()
            col_info = [
                {"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": bool(c[5])}
                for c in columns
            ]

            idx_cursor = await conn.execute(f'PRAGMA index_list("{name}")')
            indexes = await idx_cursor.fetchall()
            idx_info = [{"name": idx[1], "unique": bool(idx[2])} for idx in indexes]

            entry = {
                "name": name,
                "row_count": count,
                "columns": col_info,
                "indexes": idx_info,
            }
            if obj_type == "table":
                info.tables.append(entry)
            else:
                info.views.append(entry)

        info.last_refreshed = time.time()

    async def execute_query(
        self, db_name: str, sql: str, params: tuple = (), timeout: float = 30.0
    ) -> dict[str, Any]:
        conn = await self.get_connection(db_name)
        start = time.time()

        try:
            result = await asyncio.wait_for(
                self._run_query(conn, sql, params), timeout=timeout
            )
            elapsed = time.time() - start
            result["elapsed_seconds"] = elapsed
            return result
        except asyncio.TimeoutError:
            return {
                "error": f"Query timed out after {timeout}s",
                "elapsed_seconds": timeout,
                "rows": [],
                "columns": [],
            }
        except Exception as e:
            return {
                "error": str(e),
                "error_type": type(e).__name__,
                "elapsed_seconds": time.time() - start,
                "rows": [],
                "columns": [],
            }

    async def _run_query(
        self, conn: aiosqlite.Connection, sql: str, params: tuple
    ) -> dict:
        cursor = await conn.execute(sql, params)
        if cursor.description:
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()
            row_list = [list(r) for r in rows]

            # Infer actual column types from result data using typeof() on first non-null values
            column_types = await self._infer_result_types(conn, columns, row_list)

            return {
                "columns": columns,
                "column_types": column_types,
                "rows": row_list,
                "row_count": len(row_list),
            }
        else:
            await conn.commit()
            return {
                "columns": [],
                "column_types": [],
                "rows": [],
                "row_count": cursor.rowcount,
                "message": f"Query executed, {cursor.rowcount} rows affected",
            }

    async def _infer_result_types(
        self, conn: aiosqlite.Connection, columns: list[str], rows: list[list]
    ) -> list[str]:
        """Infer column types from actual result data rather than schema lookups.
        This correctly handles aliases, expressions, and cross-table same-named columns."""
        if not rows:
            return ["UNKNOWN"] * len(columns)

        types = []
        for col_idx in range(len(columns)):
            inferred = "NULL"
            for row in rows[:50]:
                val = row[col_idx]
                if val is None:
                    continue
                if isinstance(val, int):
                    inferred = "INTEGER"
                    break
                elif isinstance(val, float):
                    inferred = "REAL"
                    break
                elif isinstance(val, bytes):
                    inferred = "BLOB"
                    break
                else:
                    inferred = "TEXT"
                    break
            types.append(inferred)
        return types

    async def close_all(self):
        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
