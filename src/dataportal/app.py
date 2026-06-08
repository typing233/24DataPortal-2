"""Main Starlette application with all routes."""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from dataportal.config import Config
from dataportal.database import DatabaseRegistry
from dataportal.importer import CSVImporter
from dataportal.cache import TTLCache
from dataportal.sandbox import SQLSandbox

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

registry = DatabaseRegistry()
config: Config = None
importer: CSVImporter = None
cache: TTLCache = None
sandbox: SQLSandbox = None
saved_views: dict[str, list[dict]] = {}


def wants_json(request: Request) -> bool:
    path = request.url.path
    if path.endswith(".json"):
        return True
    if request.query_params.get("format") == "json":
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept


def strip_json_suffix(path: str) -> str:
    if path.endswith(".json"):
        return path[:-5]
    return path


async def startup():
    global config, importer, cache, sandbox

    config_path = os.environ.get("DATAPORTAL_CONFIG")
    config = Config(config_path)

    cache_cfg = config.cache_config
    cache = TTLCache(
        max_entries=cache_cfg.get("max_entries", 1000),
        ttl_seconds=cache_cfg.get("ttl_seconds", 60),
    )

    sandbox = SQLSandbox(config.permissions)
    importer = CSVImporter(config.import_config)

    sources = os.environ.get("DATAPORTAL_SOURCES", "").split("|")
    for source in sources:
        if not source:
            continue
        await _register_source(source)


def _apply_config_changes():
    """Apply config changes to sandbox, cache, and importer at runtime."""
    sandbox.update_permissions(config.permissions)
    cache_cfg = config.cache_config
    new_ttl = cache_cfg.get("ttl_seconds", 60)
    new_max = cache_cfg.get("max_entries", 1000)
    if new_ttl != cache._ttl or new_max != cache._max_entries:
        cache._ttl = new_ttl
        cache._max_entries = new_max
        cache.clear()
    importer._config = config.import_config


async def _register_source(source: str):
    path = Path(source).resolve()
    if path.is_dir():
        for f in sorted(path.iterdir()):
            if f.name.startswith("."):
                continue
            if f.suffix == ".sqlite" or f.suffix == ".db":
                await registry.register(f.stem, str(f), "sqlite")
            elif f.suffix == ".csv":
                await _import_csv(f)
    elif path.suffix in (".sqlite", ".db"):
        await registry.register(path.stem, str(path), "sqlite")
    elif path.suffix == ".csv":
        await _import_csv(path)


async def _import_csv(csv_path: Path):
    db_name = csv_path.stem + "_csv"
    db_path = str(csv_path.parent / f".{csv_path.stem}.sqlite")

    await importer.load_existing_hashes(db_path, db_name)
    status = await importer.import_csv(str(csv_path), db_path, db_name)

    if status.status in ("completed", "skipped"):
        await registry.register(db_name, db_path, "csv")


async def shutdown():
    await registry.close_all()


async def homepage(request: Request):
    reloaded = await config.check_reload()
    if reloaded:
        _apply_config_changes()

    dbs = []
    for name, info in registry.databases.items():
        dbs.append(info.to_dict())

    health = {
        "status": "healthy",
        "databases": len(registry.databases),
        "total_tables": sum(len(d.tables) for d in registry.databases.values()),
        "total_views": sum(len(d.views) for d in registry.databases.values()),
        "cache_entries": cache.size,
        "uptime_seconds": time.time() - _start_time,
    }

    import_statuses = {
        k: {"status": v.status, "progress": v.progress, "rows": v.rows_imported}
        for k, v in importer.status_map.items()
    }

    context = {
        "databases": dbs,
        "health": health,
        "imports": import_statuses,
        "site_config": {"site": config.site, "theme": config.theme},
    }

    if wants_json(request):
        return JSONResponse(context)

    return templates.TemplateResponse(
        request, "index.html", context
    )


async def browse_table(request: Request):
    reloaded = await config.check_reload()
    if reloaded:
        _apply_config_changes()

    db_name = request.path_params["db"]
    table_name = request.path_params["table"]
    if table_name.endswith(".json"):
        table_name = table_name[:-5]

    page = int(request.query_params.get("page", 1))
    per_page = min(int(request.query_params.get("per_page", 50)), 500)
    sort = request.query_params.get("sort", "")
    filter_col = request.query_params.get("filter_col", "")
    filter_val = request.query_params.get("filter_val", "")
    search = request.query_params.get("search", "")

    cache_key = f"browse:{db_name}:{table_name}:{page}:{per_page}:{sort}:{filter_col}:{filter_val}:{search}"
    cached = cache.get(cache_key)
    if cached:
        if wants_json(request):
            return JSONResponse(cached)
        return templates.TemplateResponse(
            request, "browse.html", dict(cached)
        )

    conn = await registry.get_connection(db_name)

    col_cursor = await conn.execute(f'PRAGMA table_info("{table_name}")')
    columns = await col_cursor.fetchall()
    col_info = [{"name": c[1], "type": c[2], "pk": bool(c[5])} for c in columns]
    col_names = [c[1] for c in columns]

    where_clauses = []
    params = []

    if filter_col and filter_val and filter_col in col_names:
        where_clauses.append(f'"{filter_col}" LIKE ?')
        params.append(f"%{filter_val}%")

    if search:
        text_cols = [c[1] for c in columns if c[2] in ("TEXT", "")]
        if text_cols:
            or_parts = [f'"{c}" LIKE ?' for c in text_cols]
            where_clauses.append(f"({' OR '.join(or_parts)})")
            params.extend([f"%{search}%"] * len(text_cols))

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    count_cursor = await conn.execute(
        f'SELECT COUNT(*) FROM "{table_name}" {where_sql}', params
    )
    count_row = await count_cursor.fetchone()
    total_rows = count_row[0]

    order_sql = ""
    if sort:
        parts = []
        for s in sort.split(","):
            s = s.strip()
            if s.startswith("-") and s[1:] in col_names:
                parts.append(f'"{s[1:]}" DESC')
            elif s in col_names:
                parts.append(f'"{s}" ASC')
        if parts:
            order_sql = "ORDER BY " + ", ".join(parts)

    offset = (page - 1) * per_page
    query = f'SELECT * FROM "{table_name}" {where_sql} {order_sql} LIMIT ? OFFSET ?'
    cursor = await conn.execute(query, params + [per_page, offset])
    rows = await cursor.fetchall()

    idx_cursor = await conn.execute(f'PRAGMA index_list("{table_name}")')
    indexes = [{"name": idx[1], "unique": bool(idx[2])} for idx in await idx_cursor.fetchall()]

    total_pages = max(1, (total_rows + per_page - 1) // per_page)

    result = {
        "database": db_name,
        "table": table_name,
        "columns": col_info,
        "rows": [list(r) for r in rows],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        "sort": sort,
        "filter_col": filter_col,
        "filter_val": filter_val,
        "search": search,
        "indexes": indexes,
        "source": registry.databases[db_name].source_type,
        "site_config": {"site": config.site, "theme": config.theme},
    }

    cache.set(result, cache_key)

    if wants_json(request):
        return JSONResponse(result)

    return templates.TemplateResponse(
        request, "browse.html", dict(result)
    )


async def save_view(request: Request):
    body = await request.json()
    db_name = body.get("database", "")
    key = f"{db_name}"
    if key not in saved_views:
        saved_views[key] = []
    saved_views[key].append({
        "name": body.get("name", "Untitled"),
        "table": body.get("table"),
        "sort": body.get("sort", ""),
        "filter_col": body.get("filter_col", ""),
        "filter_val": body.get("filter_val", ""),
        "search": body.get("search", ""),
        "created_at": time.time(),
    })
    return JSONResponse({"status": "saved", "views": saved_views[key]})


async def list_views(request: Request):
    db_name = request.path_params.get("db", "")
    views = saved_views.get(db_name, [])
    return JSONResponse({"views": views})


async def sql_editor(request: Request):
    reloaded = await config.check_reload()
    if reloaded:
        _apply_config_changes()

    db_name = request.path_params.get("db", "")
    if db_name.endswith(".json"):
        db_name = db_name[:-5]
    databases = list(registry.databases.keys())

    context = {
        "database": db_name,
        "databases": databases,
        "history": sandbox.history,
        "site_config": {"site": config.site, "theme": config.theme},
    }

    if wants_json(request):
        return JSONResponse(context)

    return templates.TemplateResponse(
        request, "sql.html", context
    )


async def sql_execute(request: Request):
    reloaded = await config.check_reload()
    if reloaded:
        _apply_config_changes()

    if request.method == "GET":
        db_name = request.query_params.get("database", "")
        sql = request.query_params.get("sql", "").strip()
    else:
        body = await request.json()
        db_name = body.get("database", "")
        sql = body.get("sql", "").strip()

    if not db_name or db_name not in registry.databases:
        return JSONResponse({"error": "Invalid database"}, status_code=400)
    if not sql:
        return JSONResponse({"error": "Empty query"}, status_code=400)

    sandbox.update_permissions(config.permissions)
    validation = sandbox.validate(sql)
    if not validation["allowed"]:
        return JSONResponse(
            {"error": validation["reason"], "allowed": False}, status_code=403
        )

    timeout = config.permissions.get("max_query_time_seconds", 30)
    max_rows = config.permissions.get("max_rows_return", 10000)

    limited_sql = sql
    if sql.strip().upper().startswith("SELECT") and "LIMIT" not in sql.upper():
        limited_sql = f"{sql} LIMIT {max_rows}"

    result = await registry.execute_query(db_name, limited_sql, timeout=timeout)
    sandbox.record(db_name, sql, result)

    if result.get("error"):
        result["explanation"] = sandbox.explain_error(result["error"])

    db_info = registry.databases[db_name]
    column_types = result.pop("column_types", [])
    result["metadata"] = {
        "database": db_name,
        "source_type": db_info.source_type,
        "source_path": db_info.path,
        "query": sql,
        "columns_detail": [
            {"name": col, "type": column_types[i] if i < len(column_types) else "UNKNOWN", "index": i}
            for i, col in enumerate(result.get("columns", []))
        ],
    }

    return JSONResponse(result)


async def sql_history(request: Request):
    return JSONResponse({"history": sandbox.history})


async def health_check(request: Request):
    return JSONResponse({
        "status": "healthy",
        "databases": len(registry.databases),
        "uptime_seconds": time.time() - _start_time,
    })


async def config_endpoint(request: Request):
    await config.check_reload()
    return JSONResponse(config.to_dict())


_start_time = time.time()


routes = [
    Route("/", homepage),
    Route("/.json", homepage),
    Route("/health", health_check),
    Route("/config.json", config_endpoint),
    Route("/db/{db}/table/{table}", browse_table),
    Route("/db/{db}/views", list_views),
    Route("/views/save", save_view, methods=["POST"]),
    Route("/sql/execute", sql_execute, methods=["GET", "POST"]),
    Route("/sql/execute.json", sql_execute, methods=["GET", "POST"]),
    Route("/sql/history", sql_history),
    Route("/sql/history.json", sql_history),
    Route("/sql", sql_editor),
    Route("/sql.json", sql_editor),
    Route("/sql/{db}", sql_editor),
    Mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"),
]

middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
]


@asynccontextmanager
async def lifespan(app):
    await startup()
    yield
    await shutdown()


app = Starlette(
    routes=routes,
    middleware=middleware,
    lifespan=lifespan,
)
