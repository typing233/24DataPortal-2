"""Permission checks for write API."""
from __future__ import annotations

from typing import Any


class WritePermissionError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def check_auth(request_headers: dict, config: dict) -> str | None:
    """Validate auth token. Returns token identifier or None if auth not required."""
    if not config.get("require_auth", True):
        return "anonymous"

    auth_header = request_headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise WritePermissionError("Missing or invalid Authorization header")

    token = auth_header[7:]
    allowed_tokens = config.get("auth_tokens", [])
    if not allowed_tokens:
        raise WritePermissionError("No auth tokens configured")
    if token not in allowed_tokens:
        raise WritePermissionError("Invalid auth token")

    return token


def check_table_permission(
    db_name: str, table_name: str, operation: str, config: dict
) -> bool:
    """Check if operation is allowed on the given table."""
    permissions = config.get("permissions", {})
    table_key = f"{db_name}.{table_name}"

    table_perms = permissions.get(table_key, permissions.get(table_name, []))

    if not table_perms:
        return True

    op_map = {
        "create": "create",
        "read": "read",
        "update": "update",
        "delete": "delete",
    }

    required = op_map.get(operation, operation)
    if required not in table_perms:
        raise WritePermissionError(
            f"Operation '{operation}' not permitted on '{table_key}'"
        )
    return True
