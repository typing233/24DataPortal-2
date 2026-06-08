"""SQL sandbox with permission control and query validation."""
import re
import time
from typing import Any


WRITE_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "REPLACE"}
DDL_KEYWORDS = {"CREATE", "DROP", "ALTER", "RENAME", "VACUUM", "REINDEX"}
DANGEROUS_KEYWORDS = {"ATTACH", "DETACH", "LOAD_EXTENSION"}


class SQLSandbox:
    def __init__(self, permissions: dict):
        self._permissions = permissions
        self._history: list[dict] = []

    @property
    def history(self) -> list[dict]:
        return self._history[-100:]

    def validate(self, sql: str) -> dict[str, Any]:
        sql_upper = sql.strip().upper()
        first_word = sql_upper.split()[0] if sql_upper.split() else ""

        for kw in DANGEROUS_KEYWORDS:
            if kw in sql_upper:
                return {"allowed": False, "reason": f"'{kw}' is not permitted"}

        if first_word in WRITE_KEYWORDS or any(kw in sql_upper for kw in WRITE_KEYWORDS):
            if first_word in WRITE_KEYWORDS and not self._permissions.get("allow_sql_write", False):
                return {"allowed": False, "reason": "Write operations are disabled"}

        if first_word in DDL_KEYWORDS:
            if not self._permissions.get("allow_sql_ddl", False):
                return {"allowed": False, "reason": "DDL operations are disabled"}

        blocked = self._permissions.get("blocked_tables", [])
        for table in blocked:
            if table.upper() in sql_upper:
                return {"allowed": False, "reason": f"Access to table '{table}' is blocked"}

        return {"allowed": True}

    def record(self, db_name: str, sql: str, result: dict):
        self._history.append({
            "db": db_name,
            "sql": sql,
            "timestamp": time.time(),
            "rows_returned": result.get("row_count", 0),
            "elapsed": result.get("elapsed_seconds", 0),
            "error": result.get("error"),
        })

    def explain_error(self, error: str) -> str:
        explanations = {
            "no such table": "The table doesn't exist. Check available tables on the homepage.",
            "no such column": "Column not found. Use PRAGMA table_info('table') to see columns.",
            "syntax error": "SQL syntax error. Check for missing commas, quotes, or keywords.",
            "UNIQUE constraint": "A row with this unique key already exists.",
            "NOT NULL constraint": "A required column was left empty.",
            "database is locked": "Another operation is in progress. Try again shortly.",
        }
        for pattern, explanation in explanations.items():
            if pattern.lower() in error.lower():
                return explanation
        return "Unexpected error. Check your SQL syntax and table/column names."
