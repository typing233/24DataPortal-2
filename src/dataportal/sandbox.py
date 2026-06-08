"""SQL sandbox with permission control and query validation."""
import re
import time
from typing import Any


WRITE_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "REPLACE"}
DDL_KEYWORDS = {"CREATE", "DROP", "ALTER", "RENAME", "VACUUM", "REINDEX"}
DANGEROUS_KEYWORDS = {"ATTACH", "DETACH", "LOAD_EXTENSION"}

_WRITE_PATTERN = re.compile(
    r'\b(INSERT\s+INTO|INSERT\s+OR\s+\w+\s+INTO|UPDATE\s|DELETE\s+FROM|REPLACE\s+INTO)\b',
    re.IGNORECASE,
)
_DDL_PATTERN = re.compile(
    r'\b(CREATE\s+(TABLE|INDEX|VIEW|TRIGGER)|DROP\s+(TABLE|INDEX|VIEW|TRIGGER)|ALTER\s+TABLE|RENAME\s|VACUUM|REINDEX)\b',
    re.IGNORECASE,
)
_DANGEROUS_PATTERN = re.compile(
    r'\b(ATTACH|DETACH|LOAD_EXTENSION)\b',
    re.IGNORECASE,
)


def _strip_comments_and_strings(sql: str) -> str:
    """Remove string literals and comments to avoid false positives on content inside them,
    but keep keywords that are part of actual SQL structure."""
    result = []
    i = 0
    while i < len(sql):
        if sql[i] == '-' and i + 1 < len(sql) and sql[i + 1] == '-':
            while i < len(sql) and sql[i] != '\n':
                i += 1
        elif sql[i] == '/' and i + 1 < len(sql) and sql[i + 1] == '*':
            i += 2
            while i + 1 < len(sql) and not (sql[i] == '*' and sql[i + 1] == '/'):
                i += 1
            i += 2
        elif sql[i] == "'":
            result.append("''")
            i += 1
            while i < len(sql):
                if sql[i] == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                elif sql[i] == "'":
                    i += 1
                    break
                else:
                    i += 1
        else:
            result.append(sql[i])
            i += 1
    return "".join(result)


class SQLSandbox:
    def __init__(self, permissions: dict):
        self._permissions = permissions
        self._history: list[dict] = []

    @property
    def history(self) -> list[dict]:
        return self._history[-100:]

    def update_permissions(self, permissions: dict):
        self._permissions = permissions

    def validate(self, sql: str) -> dict[str, Any]:
        cleaned = _strip_comments_and_strings(sql)

        if _DANGEROUS_PATTERN.search(cleaned):
            match = _DANGEROUS_PATTERN.search(cleaned)
            return {"allowed": False, "reason": f"'{match.group(1).upper()}' is not permitted"}

        if not self._permissions.get("allow_sql_write", False):
            if _WRITE_PATTERN.search(cleaned):
                return {"allowed": False, "reason": "Write operations are disabled"}

        if not self._permissions.get("allow_sql_ddl", False):
            if _DDL_PATTERN.search(cleaned):
                return {"allowed": False, "reason": "DDL operations are disabled"}

        blocked = self._permissions.get("blocked_tables", [])
        for table in blocked:
            pattern = re.compile(r'\b' + re.escape(table) + r'\b', re.IGNORECASE)
            if pattern.search(cleaned):
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
