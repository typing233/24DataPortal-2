"""Main Starlette application with all routes and plugin integration."""
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
from starlette.responses import JSONResponse, HTMLResponse, Response
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from dataportal.config import Config
from dataportal.database import DatabaseRegistry
from dataportal.importer import CSVImporter
from dataportal.cache import TTLCache
from dataportal.sandbox import SQLSandbox
from dataportal.context import AppContext
from dataportal.plugins.manager import PluginManager

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _plugin_blocks_sync(plugin_manager, injection_point: str, request=None, context=None) -> str:
    """Synchronous wrapper that collects rendered HTML blocks from plugins."""
    plugins = plugin_manager._html_block_plugins.get(injection_point, [])
    if not plugins:
        return ""
    ctx = context or {}
    if request:
        ctx.setdefault("request", request)
    parts = []
    for p in plugins:
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                html = pool.submit(asyncio.run, p.render_block(request, ctx)).result()
            parts.append(html)
        except Exception:
            pass
    return "\n".join(parts)


def _get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


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


async def _startup(app: Starlette):
    config_path = os.environ.get("DATAPORTAL_CONFIG")
    config = Config(config_path)

    cache_cfg = config.cache_config
    cache = TTLCache(
        max_entries=cache_cfg.get("max_entries", 1000),
        ttl_seconds=cache_cfg.get("ttl_seconds", 60),
    )

    sandbox = SQLSandbox(config.permissions)
    importer = CSVImporter(config.import_config)
    registry = DatabaseRegistry()
    plugin_config = config.get("plugins", default={}) or {}
    plugin_manager = PluginManager(plugin_config=plugin_config)

    state_file = Path.home() / ".dataportal" / "plugins.json"
    plugin_manager.set_state_file(state_file)

    ctx = AppContext(
        config=config,
        registry=registry,
        cache=cache,
        sandbox=sandbox,
        importer=importer,
        plugin_manager=plugin_manager,
    )
    app.state.ctx = ctx

    sources = os.environ.get("DATAPORTAL_SOURCES", "").split("|")
    for source in sources:
        if not source:
            continue
        await _register_source(ctx, source)

    # Initialize plugins
    await plugin_manager.initialize_all(ctx)
    plugin_manager.set_app(app)

    # Register plugin page routes
    for route in plugin_manager.get_page_routes():
        app.routes.insert(-1, route)

    # Register Jinja2 globals for plugin integration
    from markupsafe import Markup
    from jinja2 import pass_context

    @pass_context
    def _jinja_plugin_blocks(jinja_context, point):
        req = jinja_context.get("request")
        tpl_ctx = dict(jinja_context)
        return Markup(_plugin_blocks_sync(plugin_manager, point, req, tpl_ctx))

    templates.env.globals["plugin_blocks"] = _jinja_plugin_blocks
    templates.env.globals["plugin_nav_items"] = plugin_manager.get_nav_items
    templates.env.globals["output_formats"] = plugin_manager.get_output_formats

    # Conditionally register write API routes
    if config.get("write_api", "enabled", default=False):
        from dataportal.write import get_write_routes
        for route in get_write_routes():
            app.routes.insert(-1, route)


def _apply_config_changes(ctx: AppContext):
    ctx.sandbox.update_permissions(ctx.config.permissions)
    cache_cfg = ctx.config.cache_config
    new_ttl = cache_cfg.get("ttl_seconds", 60)
    new_max = cache_cfg.get("max_entries", 1000)
    if new_ttl != ctx.cache._ttl or new_max != ctx.cache._max_entries:
        ctx.cache._ttl = new_ttl
        ctx.cache._max_entries = new_max
        ctx.cache.clear()
    ctx.importer._config = ctx.config.import_config


async def _register_source(ctx: AppContext, source: str):
    path = Path(source).resolve()
    if path.is_dir():
        for f in sorted(path.iterdir()):
            if f.name.startswith("."):
                continue
            if f.suffix == ".sqlite" or f.suffix == ".db":
                await ctx.registry.register(f.stem, str(f), "sqlite")
            elif f.suffix == ".csv":
                await _import_csv(ctx, f)
    elif path.suffix in (".sqlite", ".db"):
        await ctx.registry.register(path.stem, str(path), "sqlite")
    elif path.suffix == ".csv":
        await _import_csv(ctx, path)


async def _import_csv(ctx: AppContext, csv_path: Path):
    db_name = csv_path.stem + "_csv"
    db_path = str(csv_path.parent / f".{csv_path.stem}.sqlite")

    await ctx.importer.load_existing_hashes(db_path, db_name)
    status = await ctx.importer.import_csv(str(csv_path), db_path, db_name)

    if status.status in ("completed", "skipped"):
        await ctx.registry.register(db_name, db_path, "csv")


async def _shutdown(app: Starlette):
    ctx: AppContext = app.state.ctx
    # Shutdown all plugins
    for name in list(ctx.plugin_manager._plugins.keys()):
        await ctx.plugin_manager.unload(name)
    await ctx.registry.close_all()


# --- Route handlers ---

async def homepage(request: Request):
    ctx = _get_ctx(request)
    reloaded = await ctx.config.check_reload()
    if reloaded:
        _apply_config_changes(ctx)

    dbs = []
    for name, info in ctx.registry.databases.items():
        dbs.append(info.to_dict())

    health = {
        "status": "healthy",
        "databases": len(ctx.registry.databases),
        "total_tables": sum(len(d.tables) for d in ctx.registry.databases.values()),
        "total_views": sum(len(d.views) for d in ctx.registry.databases.values()),
        "cache_entries": ctx.cache.size,
        "uptime_seconds": time.time() - ctx.start_time,
    }

    import_statuses = {
        k: {"status": v.status, "progress": v.progress, "rows": v.rows_imported}
        for k, v in ctx.importer.status_map.items()
    }

    context = {
        "databases": dbs,
        "health": health,
        "imports": import_statuses,
        "site_config": {"site": ctx.config.site, "theme": ctx.config.theme},
    }

    if wants_json(request):
        return JSONResponse(context)

    return templates.TemplateResponse(request, "index.html", context)


async def browse_table(request: Request):
    ctx = _get_ctx(request)
    reloaded = await ctx.config.check_reload()
    if reloaded:
        _apply_config_changes(ctx)

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

    # Check for plugin output format
    output_format = request.query_params.get("format", "")

    cache_key = f"browse:{db_name}:{table_name}:{page}:{per_page}:{sort}:{filter_col}:{filter_val}:{search}"
    cached = ctx.cache.get(cache_key)
    if cached and output_format not in ("csv", "xml"):
        if wants_json(request):
            return JSONResponse(cached)
        return templates.TemplateResponse(request, "browse.html", dict(cached))

    conn = await ctx.registry.get_connection(db_name)

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
        "source": ctx.registry.databases[db_name].source_type,
        "site_config": {"site": ctx.config.site, "theme": ctx.config.theme},
    }

    ctx.cache.set(result, cache_key)

    # Plugin output format handling
    if output_format and output_format not in ("json", "html"):
        plugin = ctx.plugin_manager.get_output_plugin(output_format)
        if plugin:
            data = plugin.render(col_names, result["rows"], {"database": db_name, "table": table_name})
            return Response(data, media_type=plugin.content_type())

    if wants_json(request):
        return JSONResponse(result)

    return templates.TemplateResponse(request, "browse.html", dict(result))


async def save_view(request: Request):
    ctx = _get_ctx(request)
    body = await request.json()
    db_name = body.get("database", "")
    key = f"{db_name}"
    if key not in ctx.saved_views:
        ctx.saved_views[key] = []
    ctx.saved_views[key].append({
        "name": body.get("name", "Untitled"),
        "table": body.get("table"),
        "sort": body.get("sort", ""),
        "filter_col": body.get("filter_col", ""),
        "filter_val": body.get("filter_val", ""),
        "search": body.get("search", ""),
        "created_at": time.time(),
    })
    return JSONResponse({"status": "saved", "views": ctx.saved_views[key]})


async def list_views(request: Request):
    ctx = _get_ctx(request)
    db_name = request.path_params.get("db", "")
    views = ctx.saved_views.get(db_name, [])
    return JSONResponse({"views": views})


async def sql_editor(request: Request):
    ctx = _get_ctx(request)
    reloaded = await ctx.config.check_reload()
    if reloaded:
        _apply_config_changes(ctx)

    db_name = request.path_params.get("db", "")
    if db_name.endswith(".json"):
        db_name = db_name[:-5]
    databases = list(ctx.registry.databases.keys())

    context = {
        "database": db_name,
        "databases": databases,
        "history": ctx.sandbox.history,
        "site_config": {"site": ctx.config.site, "theme": ctx.config.theme},
    }

    if wants_json(request):
        return JSONResponse(context)

    return templates.TemplateResponse(request, "sql.html", context)


async def sql_execute(request: Request):
    ctx = _get_ctx(request)
    reloaded = await ctx.config.check_reload()
    if reloaded:
        _apply_config_changes(ctx)

    if request.method == "GET":
        db_name = request.query_params.get("database", "")
        sql = request.query_params.get("sql", "").strip()
    else:
        body = await request.json()
        db_name = body.get("database", "")
        sql = body.get("sql", "").strip()

    if not db_name or db_name not in ctx.registry.databases:
        return JSONResponse({"error": "Invalid database"}, status_code=400)
    if not sql:
        return JSONResponse({"error": "Empty query"}, status_code=400)

    ctx.sandbox.update_permissions(ctx.config.permissions)
    validation = ctx.sandbox.validate(sql)
    if not validation["allowed"]:
        return JSONResponse(
            {"error": validation["reason"], "allowed": False}, status_code=403
        )

    # Apply SQL filter plugins (pre-processing)
    for filter_plugin in ctx.plugin_manager.get_sql_filters():
        sql = filter_plugin.pre_process(sql, {"database": db_name})

    timeout = ctx.config.permissions.get("max_query_time_seconds", 30)
    max_rows = ctx.config.permissions.get("max_rows_return", 10000)

    limited_sql = sql
    if sql.strip().upper().startswith("SELECT") and "LIMIT" not in sql.upper():
        limited_sql = f"{sql} LIMIT {max_rows}"

    result = await ctx.registry.execute_query(db_name, limited_sql, timeout=timeout)
    ctx.sandbox.record(db_name, sql, result)

    if result.get("error"):
        result["explanation"] = ctx.sandbox.explain_error(result["error"])

    # Apply SQL filter plugins (post-processing / masking)
    if not result.get("error") and result.get("columns"):
        for filter_plugin in ctx.plugin_manager.get_sql_filters():
            result["columns"], result["rows"] = filter_plugin.post_process(
                result["columns"], result["rows"], {"database": db_name}
            )

    db_info = ctx.registry.databases[db_name]
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

    # Plugin output format
    output_format = request.query_params.get("format", "")
    if output_format and output_format not in ("json", "html"):
        plugin = ctx.plugin_manager.get_output_plugin(output_format)
        if plugin:
            data = plugin.render(
                result.get("columns", []),
                result.get("rows", []),
                result.get("metadata", {}),
            )
            return Response(data, media_type=plugin.content_type())

    return JSONResponse(result)


async def sql_history(request: Request):
    ctx = _get_ctx(request)
    return JSONResponse({"history": ctx.sandbox.history})


async def health_check(request: Request):
    ctx = _get_ctx(request)
    plugin_health = []
    for info in ctx.plugin_manager.list_plugins():
        plugin_health.append({"name": info["name"], "state": info["state"]})

    return JSONResponse({
        "status": "healthy",
        "databases": len(ctx.registry.databases),
        "uptime_seconds": time.time() - ctx.start_time,
        "plugins": plugin_health,
    })


async def config_endpoint(request: Request):
    ctx = _get_ctx(request)
    await ctx.config.check_reload()
    return JSONResponse(ctx.config.to_dict())


async def plugins_endpoint(request: Request):
    ctx = _get_ctx(request)
    return JSONResponse({"plugins": ctx.plugin_manager.list_plugins()})


async def plugin_manage(request: Request):
    """POST /plugins/manage - hot-plug management: enable, disable, reload, unload, load."""
    ctx = _get_ctx(request)
    body = await request.json()
    action = body.get("action", "")
    name = body.get("name", "")

    if not name:
        return JSONResponse({"error": "Plugin name required"}, status_code=400)

    if action == "enable":
        await ctx.plugin_manager.enable(name)
        await ctx.plugin_manager.discover()
        valid, reasons = await ctx.plugin_manager.validate(name)
        if valid:
            instance = await ctx.plugin_manager.load(name)
            if instance:
                await ctx.plugin_manager.initialize(name, ctx)
        info = ctx.plugin_manager.get_plugin_info(name)
        return JSONResponse({"status": "enabled", "plugin": info})

    elif action == "disable":
        await ctx.plugin_manager.disable(name)
        return JSONResponse({"status": "disabled", "plugin": ctx.plugin_manager.get_plugin_info(name)})

    elif action == "reload":
        success = await ctx.plugin_manager.reload(name, ctx)
        info = ctx.plugin_manager.get_plugin_info(name)
        if success:
            return JSONResponse({"status": "reloaded", "plugin": info})
        return JSONResponse({"status": "failed", "plugin": info}, status_code=500)

    elif action == "unload":
        await ctx.plugin_manager.unload(name)
        return JSONResponse({"status": "unloaded", "plugin": ctx.plugin_manager.get_plugin_info(name)})

    elif action == "load":
        await ctx.plugin_manager.discover()
        valid, reasons = await ctx.plugin_manager.validate(name)
        if not valid:
            return JSONResponse({"error": f"Validation failed: {reasons}"}, status_code=400)
        instance = await ctx.plugin_manager.load(name)
        if not instance:
            return JSONResponse({"error": "Load failed"}, status_code=500)
        success = await ctx.plugin_manager.initialize(name, ctx)
        info = ctx.plugin_manager.get_plugin_info(name)
        if success:
            return JSONResponse({"status": "loaded", "plugin": info})
        return JSONResponse({"status": "failed", "plugin": info}, status_code=500)

    elif action == "health":
        result = await ctx.plugin_manager.health_check(name)
        return JSONResponse({"status": "ok", "health": result})

    else:
        return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)


# --- App factory ---

def create_app() -> Starlette:
    routes = [
        Route("/", homepage),
        Route("/.json", homepage),
        Route("/health", health_check),
        Route("/config.json", config_endpoint),
        Route("/plugins.json", plugins_endpoint),
        Route("/plugins/manage", plugin_manage, methods=["POST"]),
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
    async def lifespan(application):
        await _startup(application)
        yield
        await _shutdown(application)

    return Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )


app = create_app()
