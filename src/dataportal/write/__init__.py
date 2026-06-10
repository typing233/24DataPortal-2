"""Data write API - configurable CRUD operations with transactions, concurrency control, and audit."""
from __future__ import annotations

from starlette.routing import Route

from dataportal.write.handlers import (
    create_row,
    update_row,
    delete_row,
    batch_operations,
)


def get_write_routes() -> list[Route]:
    return [
        Route("/api/db/{db}/table/{table}", create_row, methods=["POST"]),
        Route("/api/db/{db}/table/{table}/{pk}", update_row, methods=["PUT"]),
        Route("/api/db/{db}/table/{table}/{pk}", delete_row, methods=["DELETE"]),
        Route("/api/db/{db}/table/{table}/_batch", batch_operations, methods=["POST"]),
    ]
