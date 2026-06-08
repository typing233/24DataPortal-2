"""SQL sandbox with permission control and query validation."""
import re
import time
from typing import Any


def _strip_comments_and_strings(sql: str) -> str:
    """Strip comments and string literals, replacing them with safe placeholders."""
    result = []
    i = 0
    n = len(sql)
    while i < n:
        # Single-line comment
        if sql[i] == '-' and i + 1 < n and sql[i + 1] == '-':
            while i < n and sql[i] != '\n':
                i += 1
        # Block comment
        elif sql[i] == '/' and i + 1 < n and sql[i + 1] == '*':
            i += 2
            while i + 1 < n and not (sql[i] == '*' and sql[i + 1] == '/'):
                i += 1
            i += 2
        # Single-quoted string
        elif sql[i] == "'":
            result.append("'_'")
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                elif sql[i] == "'":
                    i += 1
                    break
                else:
                    i += 1
        # Double-quoted identifier (keep as-is since it's a name, not a value)
        elif sql[i] == '"':
            result.append(sql[i])
            i += 1
            while i < n:
                if sql[i] == '"':
                    result.append(sql[i])
                    i += 1
                    break
                else:
                    result.append(sql[i])
                    i += 1
        else:
            result.append(sql[i])
            i += 1
    return "".join(result)


# Matches any write DML regardless of position (handles WITH...INSERT, comments before, etc.)
_WRITE_PATTERNS = [
    re.compile(r'\bINSERT\s+(OR\s+\w+\s+)?INTO\b', re.IGNORECASE),
    re.compile(r'\bINSERT\s+INTO\b', re.IGNORECASE),
    re.compile(r'\bREPLACE\s+INTO\b', re.IGNORECASE),
    re.compile(r'\bUPDATE\b(?!\s*\()', re.IGNORECASE),  # UPDATE but not UPDATE() function
    re.compile(r'\bDELETE\s+FROM\b', re.IGNORECASE),
    re.compile(r'\bDELETE\b(?!\s*\()', re.IGNORECASE),  # bare DELETE
    re.compile(r'\bUPSERT\b', re.IGNORECASE),
    re.compile(r'\bMERGE\b', re.IGNORECASE),
]

_DDL_PATTERNS = [
    re.compile(r'\bCREATE\s+(TEMP\s+|TEMPORARY\s+)?(TABLE|INDEX|VIEW|TRIGGER|VIRTUAL\s+TABLE)\b', re.IGNORECASE),
    re.compile(r'\bDROP\s+(TABLE|INDEX|VIEW|TRIGGER)\b', re.IGNORECASE),
    re.compile(r'\bDROP\s+IF\s+EXISTS\b', re.IGNORECASE),
    re.compile(r'\bALTER\s+TABLE\b', re.IGNORECASE),
    re.compile(r'\bVACUUM\b', re.IGNORECASE),
    re.compile(r'\bREINDEX\b', re.IGNORECASE),
    re.compile(r'\bANALYZE\b', re.IGNORECASE),
]

_DANGEROUS_PATTERNS = [
    re.compile(r'\bATTACH\b', re.IGNORECASE),
    re.compile(r'\bDETACH\b', re.IGNORECASE),
    re.compile(r'\bLOAD_EXTENSION\b', re.IGNORECASE),
]

# Multi-statement detection: semicolons that aren't inside strings
_SEMICOLON_SPLIT = re.compile(r';')


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

        # Block multi-statement queries (semicolons followed by non-whitespace)
        statements = [s.strip() for s in _SEMICOLON_SPLIT.split(cleaned) if s.strip()]
        if len(statements) > 1:
            return {"allowed": False, "reason": "Multi-statement queries are not permitted"}

        # Dangerous operations (always blocked)
        for pattern in _DANGEROUS_PATTERNS:
            m = pattern.search(cleaned)
            if m:
                return {"allowed": False, "reason": f"'{m.group(0).strip().upper()}' is not permitted"}

        # Write operations
        if not self._permissions.get("allow_sql_write", False):
            for pattern in _WRITE_PATTERNS:
                if pattern.search(cleaned):
                    return {"allowed": False, "reason": "Write operations are disabled"}

        # DDL operations
        if not self._permissions.get("allow_sql_ddl", False):
            for pattern in _DDL_PATTERNS:
                if pattern.search(cleaned):
                    return {"allowed": False, "reason": "DDL operations are disabled"}

        # Blocked tables
        blocked = self._permissions.get("blocked_tables", [])
        for table in blocked:
            pattern = re.compile(r'\b' + re.escape(table) + r'\b', re.IGNORECASE)
            if pattern.search(cleaned):
                return {"allowed": False, "reason": f"Access to table '{table}' is blocked"}

        # PRAGMA writes (some PRAGMAs modify state)
        if re.search(r'\bPRAGMA\b', cleaned, re.IGNORECASE):
            # Allow read-only PRAGMAs (those with no assignment)
            if re.search(r'\bPRAGMA\s+\w+\s*=', cleaned, re.IGNORECASE):
                if not self._permissions.get("allow_sql_write", False):
                    return {"allowed": False, "reason": "PRAGMA writes are disabled"}

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
