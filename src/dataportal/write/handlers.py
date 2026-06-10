"""Write API request handlers with unified idempotency and concurrency control."""
from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from dataportal.write.permissions import check_auth, check_table_permission, WritePermissionError
from dataportal.write.audit import ensure_audit_table, log_mutation
from dataportal.write.concurrency import (
    get_row_with_version,
    check_version,
    has_version_column,
    ConcurrencyConflictError,
)
from dataportal.write.idempotency import (
    ensure_idempotency_table,
    get_cached_response,
    store_response,
)


def _get_ctx(request: Request):
    return request.app.state.ctx


def _get_write_config(request: Request) -> dict:
    ctx = _get_ctx(request)
    return ctx.config.get("write_api", default={}) or {}


async def _get_pk_column(conn, table_name: str) -> str | None:
    cursor = await conn.execute(f'PRAGMA table_info("{table_name}")')
    columns = await cursor.fetchall()
    for col in columns:
        if col[5]:  # pk flag
            return col[1]
    return None


async def _check_idempotency(conn, request: Request, write_config: dict) -> tuple[str | None, dict | None]:
    """Check idempotency key. Returns (key, cached_response) or (key, None) if not cached."""
    idempotency_key = request.headers.get("idempotency-key")
    if not idempotency_key:
        return None, None
    await ensure_idempotency_table(conn)
    window = write_config.get("idempotency_window_seconds", 3600)
    cached = await get_cached_response(conn, idempotency_key, window)
    return idempotency_key, cached


async def _store_idempotent_response(conn, key: str | None, status: int, body: dict):
    """Store response for idempotency dedup if key is present."""
    if key:
        await store_response(conn, key, status, body)


async def create_row(request: Request):
    """POST /api/db/{db}/table/{table} - Create one or more rows."""
    ctx = _get_ctx(request)
    write_config = _get_write_config(request)

    try:
        token = check_auth(dict(request.headers), write_config)
    except WritePermissionError as e:
        return JSONResponse({"error": e.message}, status_code=401)

    db_name = request.path_params["db"]
    table_name = request.path_params["table"]

    try:
        check_table_permission(db_name, table_name, "create", write_config)
    except WritePermissionError as e:
        return JSONResponse({"error": e.message}, status_code=403)

    if db_name not in ctx.registry.databases:
        return JSONResponse({"error": "Database not found"}, status_code=404)

    body = await request.json()
    conn = await ctx.registry.get_connection(db_name)

    # Idempotency check
    idempotency_key, cached = await _check_idempotency(conn, request, write_config)
    if cached:
        return JSONResponse(cached["body"], status_code=cached["status"])

    rows_data = body if isinstance(body, list) else [body]
    if not rows_data:
        return JSONResponse({"error": "No data provided"}, status_code=400)

    columns = list(rows_data[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_str = ", ".join(f'"{c}"' for c in columns)
    sql = f'INSERT INTO "{table_name}" ({col_str}) VALUES ({placeholders})'

    if write_config.get("audit_log", True):
        await ensure_audit_table(conn)

    try:
        inserted_ids = []
        for row in rows_data:
            values = [row.get(c) for c in columns]
            cursor = await conn.execute(sql, values)
            inserted_ids.append(cursor.lastrowid)

            if write_config.get("audit_log", True):
                await log_mutation(
                    conn, "CREATE", db_name, table_name,
                    row_pk=str(cursor.lastrowid),
                    after_data=row,
                    user_token=token,
                    idempotency_key=idempotency_key,
                )

        await conn.commit()
        response_body = {"status": "created", "ids": inserted_ids, "count": len(inserted_ids)}
        status_code = 201

        await _store_idempotent_response(conn, idempotency_key, status_code, response_body)
        return JSONResponse(response_body, status_code=status_code)
    except Exception as e:
        await conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)


async def update_row(request: Request):
    """PUT /api/db/{db}/table/{table}/{pk} - Update a row with concurrency control."""
    ctx = _get_ctx(request)
    write_config = _get_write_config(request)

    try:
        token = check_auth(dict(request.headers), write_config)
    except WritePermissionError as e:
        return JSONResponse({"error": e.message}, status_code=401)

    db_name = request.path_params["db"]
    table_name = request.path_params["table"]
    pk_value = request.path_params["pk"]

    try:
        check_table_permission(db_name, table_name, "update", write_config)
    except WritePermissionError as e:
        return JSONResponse({"error": e.message}, status_code=403)

    if db_name not in ctx.registry.databases:
        return JSONResponse({"error": "Database not found"}, status_code=404)

    conn = await ctx.registry.get_connection(db_name)

    # Idempotency check
    idempotency_key, cached = await _check_idempotency(conn, request, write_config)
    if cached:
        return JSONResponse(cached["body"], status_code=cached["status"])

    pk_column = await _get_pk_column(conn, table_name)
    if not pk_column:
        return JSONResponse({"error": "Table has no primary key"}, status_code=400)

    body = await request.json()

    # Concurrency control via If-Match header
    if_match = request.headers.get("if-match")
    use_version = await has_version_column(conn, table_name)

    try:
        if use_version and if_match:
            await check_version(conn, table_name, pk_column, pk_value, int(if_match))

        before_row, _ = await get_row_with_version(conn, table_name, pk_column, pk_value)
        if before_row is None:
            return JSONResponse({"error": "Row not found"}, status_code=404)

        set_clauses = []
        values = []
        for col, val in body.items():
            if col == pk_column or col == "_version":
                continue
            set_clauses.append(f'"{col}" = ?')
            values.append(val)

        if use_version:
            set_clauses.append("_version = _version + 1")

        if not set_clauses:
            return JSONResponse({"error": "No fields to update"}, status_code=400)

        sql = f'UPDATE "{table_name}" SET {", ".join(set_clauses)} WHERE "{pk_column}" = ?'
        values.append(pk_value)

        await conn.execute(sql, values)

        if write_config.get("audit_log", True):
            await ensure_audit_table(conn)
            await log_mutation(
                conn, "UPDATE", db_name, table_name,
                row_pk=str(pk_value),
                before_data=before_row,
                after_data=body,
                user_token=token,
                idempotency_key=idempotency_key,
            )

        await conn.commit()

        after_row, new_version = await get_row_with_version(conn, table_name, pk_column, pk_value)
        response_body = {"status": "updated", "row": after_row}
        if new_version is not None:
            response_body["version"] = new_version

        await _store_idempotent_response(conn, idempotency_key, 200, response_body)
        return JSONResponse(response_body)

    except ConcurrencyConflictError as e:
        return JSONResponse({"error": e.message}, status_code=409)
    except Exception as e:
        await conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)


async def delete_row(request: Request):
    """DELETE /api/db/{db}/table/{table}/{pk} - Delete a row with concurrency control."""
    ctx = _get_ctx(request)
    write_config = _get_write_config(request)

    try:
        token = check_auth(dict(request.headers), write_config)
    except WritePermissionError as e:
        return JSONResponse({"error": e.message}, status_code=401)

    db_name = request.path_params["db"]
    table_name = request.path_params["table"]
    pk_value = request.path_params["pk"]

    try:
        check_table_permission(db_name, table_name, "delete", write_config)
    except WritePermissionError as e:
        return JSONResponse({"error": e.message}, status_code=403)

    if db_name not in ctx.registry.databases:
        return JSONResponse({"error": "Database not found"}, status_code=404)

    conn = await ctx.registry.get_connection(db_name)

    # Idempotency check
    idempotency_key, cached = await _check_idempotency(conn, request, write_config)
    if cached:
        return JSONResponse(cached["body"], status_code=cached["status"])

    pk_column = await _get_pk_column(conn, table_name)
    if not pk_column:
        return JSONResponse({"error": "Table has no primary key"}, status_code=400)

    # Concurrency control
    if_match = request.headers.get("if-match")
    use_version = await has_version_column(conn, table_name)

    try:
        if use_version and if_match:
            await check_version(conn, table_name, pk_column, pk_value, int(if_match))

        before_row, _ = await get_row_with_version(conn, table_name, pk_column, pk_value)
        if before_row is None:
            return JSONResponse({"error": "Row not found"}, status_code=404)

        await conn.execute(
            f'DELETE FROM "{table_name}" WHERE "{pk_column}" = ?', (pk_value,)
        )

        if write_config.get("audit_log", True):
            await ensure_audit_table(conn)
            await log_mutation(
                conn, "DELETE", db_name, table_name,
                row_pk=str(pk_value),
                before_data=before_row,
                user_token=token,
                idempotency_key=idempotency_key,
            )

        await conn.commit()
        response_body = {"status": "deleted", "pk": pk_value}
        await _store_idempotent_response(conn, idempotency_key, 200, response_body)
        return JSONResponse(response_body)

    except ConcurrencyConflictError as e:
        return JSONResponse({"error": e.message}, status_code=409)
    except Exception as e:
        await conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)


async def batch_operations(request: Request):
    """POST /api/db/{db}/table/{table}/_batch - Transactional batch with concurrency control."""
    ctx = _get_ctx(request)
    write_config = _get_write_config(request)

    try:
        token = check_auth(dict(request.headers), write_config)
    except WritePermissionError as e:
        return JSONResponse({"error": e.message}, status_code=401)

    db_name = request.path_params["db"]
    table_name = request.path_params["table"]

    if db_name not in ctx.registry.databases:
        return JSONResponse({"error": "Database not found"}, status_code=404)

    conn = await ctx.registry.get_connection(db_name)

    # Idempotency check for entire batch
    idempotency_key, cached = await _check_idempotency(conn, request, write_config)
    if cached:
        return JSONResponse(cached["body"], status_code=cached["status"])

    body = await request.json()
    operations = body.get("operations", [])
    if not operations:
        return JSONResponse({"error": "No operations provided"}, status_code=400)

    pk_column = await _get_pk_column(conn, table_name)
    use_version = await has_version_column(conn, table_name)

    if write_config.get("audit_log", True):
        await ensure_audit_table(conn)

    results = []
    try:
        await conn.execute("BEGIN IMMEDIATE")

        for op in operations:
            op_type = op.get("operation", "").lower()
            data = op.get("data", {})
            pk = op.get("pk")
            expected_version = op.get("version")  # per-operation concurrency control

            try:
                check_table_permission(db_name, table_name, op_type, write_config)
            except WritePermissionError as e:
                raise Exception(f"Permission denied: {e.message}")

            if op_type == "create":
                columns = list(data.keys())
                placeholders = ", ".join(["?"] * len(columns))
                col_str = ", ".join(f'"{c}"' for c in columns)
                values = [data.get(c) for c in columns]
                cursor = await conn.execute(
                    f'INSERT INTO "{table_name}" ({col_str}) VALUES ({placeholders})', values
                )
                results.append({"operation": "create", "id": cursor.lastrowid})

                if write_config.get("audit_log", True):
                    await log_mutation(
                        conn, "CREATE", db_name, table_name,
                        row_pk=str(cursor.lastrowid), after_data=data, user_token=token,
                    )

            elif op_type == "update" and pk and pk_column:
                # Concurrency check within batch
                if use_version and expected_version is not None:
                    await check_version(conn, table_name, pk_column, pk, int(expected_version))

                set_clauses = []
                values = []
                for col, val in data.items():
                    if col == pk_column or col == "_version":
                        continue
                    set_clauses.append(f'"{col}" = ?')
                    values.append(val)
                if use_version:
                    set_clauses.append("_version = _version + 1")
                if not set_clauses:
                    raise Exception(f"No fields to update for pk={pk}")
                values.append(pk)
                cursor = await conn.execute(
                    f'UPDATE "{table_name}" SET {", ".join(set_clauses)} WHERE "{pk_column}" = ?',
                    values,
                )
                if cursor.rowcount == 0:
                    raise Exception(f"Row not found: pk={pk}")
                results.append({"operation": "update", "pk": pk})

                if write_config.get("audit_log", True):
                    await log_mutation(
                        conn, "UPDATE", db_name, table_name,
                        row_pk=str(pk), after_data=data, user_token=token,
                    )

            elif op_type == "delete" and pk and pk_column:
                # Concurrency check within batch
                if use_version and expected_version is not None:
                    await check_version(conn, table_name, pk_column, pk, int(expected_version))

                cursor = await conn.execute(
                    f'DELETE FROM "{table_name}" WHERE "{pk_column}" = ?', (pk,)
                )
                if cursor.rowcount == 0:
                    raise Exception(f"Row not found: pk={pk}")
                results.append({"operation": "delete", "pk": pk})

                if write_config.get("audit_log", True):
                    await log_mutation(
                        conn, "DELETE", db_name, table_name,
                        row_pk=str(pk), user_token=token,
                    )
            else:
                raise Exception(f"Invalid operation: {op_type}")

        await conn.commit()
        response_body = {"status": "completed", "results": results, "count": len(results)}
        await _store_idempotent_response(conn, idempotency_key, 200, response_body)
        return JSONResponse(response_body)

    except ConcurrencyConflictError as e:
        await conn.rollback()
        return JSONResponse({"error": e.message, "results": results}, status_code=409)
    except Exception as e:
        await conn.rollback()
        return JSONResponse({"error": str(e), "results": results}, status_code=400)
