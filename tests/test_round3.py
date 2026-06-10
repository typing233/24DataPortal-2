"""Tests for round-3 fixes: PagePlugin hot-routing, HTMLBlockPlugin context, idempotency scoping."""
import asyncio
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from dataportal.plugins.base import (
    BasePlugin,
    HTMLBlockPlugin,
    PagePlugin,
    PluginMeta,
    PluginState,
)
from dataportal.plugins.manager import PluginManager, PluginEntry
from dataportal.plugins.discovery import PluginCandidate
from dataportal.plugins.sandbox import SandboxContext, PROTECTED_PACKAGES


# --- Fixture: a test app with write API ---

@pytest.fixture(scope="module")
def app_env():
    """Create a test environment with write API enabled."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test.sqlite"

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT,
            _version INTEGER DEFAULT 1
        )
    """)
    conn.execute("INSERT INTO notes (title, body) VALUES ('Note1', 'Content1')")
    conn.execute("INSERT INTO notes (title, body) VALUES ('Note2', 'Content2')")
    conn.commit()
    conn.close()

    config_path = Path(tmp) / "config.json"
    config_path.write_text(json.dumps({
        "write_api": {
            "enabled": True,
            "require_auth": True,
            "auth_tokens": ["token-xyz"],
            "permissions": {
                "test.notes": ["read", "create", "update", "delete"],
            },
            "audit_log": False,
            "idempotency_window_seconds": 3600,
        }
    }))

    return str(db_path), str(config_path), tmp


@pytest.fixture(scope="module")
def client(app_env):
    db_path, config_path, tmp = app_env
    os.environ["DATAPORTAL_SOURCES"] = db_path
    os.environ["DATAPORTAL_CONFIG"] = config_path

    import importlib
    import dataportal.app
    importlib.reload(dataportal.app)
    from dataportal.app import app

    with TestClient(app) as c:
        yield c

    del os.environ["DATAPORTAL_SOURCES"]
    del os.environ["DATAPORTAL_CONFIG"]


AUTH = {"Authorization": "Bearer token-xyz"}


# ============================================================
# Task #13 - PagePlugin hot-routing tests
# ============================================================

class _TestPagePlugin(PagePlugin):
    meta = PluginMeta(name="test-page", version="1.0.0")

    async def initialize(self, ctx):
        pass

    async def health_check(self):
        return {"status": "healthy"}

    async def shutdown(self):
        pass

    def get_routes(self):
        async def _handler(request):
            return PlainTextResponse("page-plugin-ok")
        return [Route("/test-plugin-page", _handler)]

    def nav_items(self):
        return [{"url": "/test-plugin-page", "label": "Test Page"}]


class TestPagePluginHotRouting:
    def test_load_adds_route_and_unload_removes(self, client):
        """After loading a PagePlugin, its route is accessible. After unload, it 404s."""
        app = client.app
        manager = app.state.ctx.plugin_manager

        # Create a fake candidate for the test page plugin
        candidate = PluginCandidate(
            name="test-page",
            entry_point=None,
            module_path="tests.test_round3:_TestPagePlugin",
            plugin_class=_TestPagePlugin,
            meta=PluginMeta(name="test-page", version="1.0.0"),
            error=None,
        )
        entry = PluginEntry(candidate)
        manager._plugins["test-page"] = entry

        # Load and initialize via the manage endpoint
        resp = client.post("/plugins/manage", json={"action": "load", "name": "test-page"})
        assert resp.status_code == 200

        # Route should now be accessible
        resp = client.get("/test-plugin-page")
        assert resp.status_code == 200
        assert resp.text == "page-plugin-ok"

        # Unload via the manage endpoint
        resp = client.post("/plugins/manage", json={"action": "unload", "name": "test-page"})
        assert resp.status_code == 200

        # Route should now 404
        resp = client.get("/test-plugin-page")
        assert resp.status_code == 404

    def test_disable_removes_route(self, client):
        """Disabling a loaded PagePlugin removes its route via the manage API."""
        # Use the manage endpoint which runs in the app's event loop
        app = client.app
        manager = app.state.ctx.plugin_manager

        # Define a page plugin with a unique path
        class _LP2(PagePlugin):
            meta = PluginMeta(name="test-page-dis", version="1.0.0")
            async def initialize(self, ctx): pass
            async def health_check(self): return {"status": "healthy"}
            async def shutdown(self): pass
            def get_routes(self):
                async def _h(request):
                    return PlainTextResponse("dis-ok")
                return [Route("/test-plugin-dis", _h)]
            def nav_items(self): return []

        candidate = PluginCandidate(
            name="test-page-dis",
            entry_point=None,
            module_path="tests.test_round3:_x",
            plugin_class=_LP2,
            meta=PluginMeta(name="test-page-dis", version="1.0.0"),
            error=None,
        )
        entry = PluginEntry(candidate)
        manager._plugins["test-page-dis"] = entry

        # Use enable action which does discover+validate+load+initialize in app's loop
        resp = client.post("/plugins/manage", json={"action": "enable", "name": "test-page-dis"})
        assert resp.status_code == 200, resp.json()

        # Route accessible
        resp = client.get("/test-plugin-dis")
        assert resp.status_code == 200
        assert resp.text == "dis-ok"

        # Disable via manage endpoint
        resp = client.post("/plugins/manage", json={"action": "disable", "name": "test-page-dis"})
        assert resp.status_code == 200

        # Route gone
        resp = client.get("/test-plugin-dis")
        assert resp.status_code == 404


# ============================================================
# Task #14 - HTMLBlockPlugin context tests
# ============================================================

class _TestHTMLBlockPlugin(HTMLBlockPlugin):
    meta = PluginMeta(name="test-block", version="1.0.0")
    received_request = None
    received_context = None

    async def initialize(self, ctx):
        pass

    async def health_check(self):
        return {"status": "healthy"}

    async def shutdown(self):
        pass

    def injection_point(self):
        return "index.after_stats"

    async def render_block(self, request, context):
        _TestHTMLBlockPlugin.received_request = request
        _TestHTMLBlockPlugin.received_context = context
        db_count = context.get("health", {}).get("databases", "?")
        return f'<div class="test-block">DBs: {db_count}</div>'


class TestHTMLBlockPluginContext:
    def test_render_block_receives_request_and_context(self, client):
        """HTMLBlockPlugin.render_block gets the actual request and template context."""
        app = client.app
        manager = app.state.ctx.plugin_manager

        candidate = PluginCandidate(
            name="test-block",
            entry_point=None,
            module_path="tests.test_round3:_TestHTMLBlockPlugin",
            plugin_class=_TestHTMLBlockPlugin,
            meta=PluginMeta(name="test-block", version="1.0.0"),
            error=None,
        )
        entry = PluginEntry(candidate)
        manager._plugins["test-block"] = entry

        # Load via manage endpoint
        resp = client.post("/plugins/manage", json={"action": "load", "name": "test-block"})
        assert resp.status_code == 200

        # Request the homepage which uses {{ plugin_blocks("index.after_stats") }}
        resp = client.get("/", headers={"Accept": "text/html"})
        assert resp.status_code == 200

        # The plugin should have been called with a real request and context
        assert _TestHTMLBlockPlugin.received_request is not None
        assert _TestHTMLBlockPlugin.received_context is not None
        assert "health" in _TestHTMLBlockPlugin.received_context
        # The rendered block should appear in the HTML
        assert "test-block" in resp.text

        # Cleanup
        client.post("/plugins/manage", json={"action": "unload", "name": "test-block"})


# ============================================================
# Task #15 - Sandbox module isolation test
# ============================================================

class TestSandboxModuleIsolation:
    def test_protected_packages_not_replaceable(self):
        """Plugins can't replace core packages in sys.modules."""
        import sys
        import starlette

        original_starlette = sys.modules["starlette"]

        with SandboxContext():
            # Simulate a plugin trying to replace starlette
            sys.modules["starlette"] = "fake"

        # After sandbox exits, starlette should be restored
        assert sys.modules["starlette"] is original_starlette

    def test_sandbox_allows_new_modules(self):
        """Plugins can load new (non-blocked) modules within sandbox."""
        import sys
        test_mod_name = "_test_sandbox_module_xyz"
        # Make sure it's not loaded
        sys.modules.pop(test_mod_name, None)

        import types
        with SandboxContext():
            fake = types.ModuleType(test_mod_name)
            sys.modules[test_mod_name] = fake

        # New modules stay (they're not protected)
        assert test_mod_name in sys.modules
        del sys.modules[test_mod_name]


# ============================================================
# Task #17 - Idempotency key scoping tests
# ============================================================

class TestIdempotencyScoping:
    def test_same_key_different_method_not_deduped(self, client):
        """Same idempotency-key header with different HTTP methods are independent."""
        key = "scope-test-001"

        # Create a row
        resp1 = client.post(
            "/api/db/test/table/notes",
            json={"title": "ScopeTest", "body": "x"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp1.status_code == 201
        pk = resp1.json()["ids"][0]

        # Update with same key - should NOT be deduped (different method+path)
        resp2 = client.put(
            f"/api/db/test/table/notes/{pk}",
            json={"title": "Updated"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "updated"

    def test_same_key_different_body_not_deduped(self, client):
        """Same key + same method + same path but different body are independent."""
        key = "scope-test-002"

        resp1 = client.post(
            "/api/db/test/table/notes",
            json={"title": "Body1", "body": "a"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            "/api/db/test/table/notes",
            json={"title": "Body2", "body": "b"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp2.status_code == 201
        # Different bodies mean different scoped keys, so both create
        assert resp2.json()["ids"] != resp1.json()["ids"]

    def test_same_key_same_request_deduped(self, client):
        """Same key + same method + same path + same body IS deduped."""
        key = "scope-test-003"
        body = {"title": "Dedup", "body": "same"}

        resp1 = client.post(
            "/api/db/test/table/notes",
            json=body,
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            "/api/db/test/table/notes",
            json=body,
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp2.status_code == 201
        assert resp2.json()["ids"] == resp1.json()["ids"]

    def test_update_idempotency(self, client):
        """Update operation with idempotency key returns cached response on replay."""
        # Create a row first
        resp = client.post(
            "/api/db/test/table/notes",
            json={"title": "ForUpdate", "body": "orig"},
            headers=AUTH,
        )
        pk = resp.json()["ids"][0]

        key = "scope-test-update-001"
        update_body = {"title": "Changed"}

        resp1 = client.put(
            f"/api/db/test/table/notes/{pk}",
            json=update_body,
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp1.status_code == 200

        resp2 = client.put(
            f"/api/db/test/table/notes/{pk}",
            json=update_body,
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp2.status_code == 200
        assert resp2.json() == resp1.json()

    def test_delete_idempotency(self, client):
        """Delete operation with idempotency key returns cached response on replay."""
        # Create a row
        resp = client.post(
            "/api/db/test/table/notes",
            json={"title": "ForDelete", "body": "x"},
            headers=AUTH,
        )
        pk = resp.json()["ids"][0]

        key = "scope-test-delete-001"

        resp1 = client.delete(
            f"/api/db/test/table/notes/{pk}",
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "deleted"

        # Replay - should return cached instead of 404
        resp2 = client.delete(
            f"/api/db/test/table/notes/{pk}",
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "deleted"

    def test_batch_idempotency(self, client):
        """Batch operation with idempotency key returns cached response on replay."""
        key = "scope-test-batch-001"
        batch_body = {
            "operations": [
                {"operation": "create", "data": {"title": "Batch1", "body": "x"}},
                {"operation": "create", "data": {"title": "Batch2", "body": "y"}},
            ]
        }

        resp1 = client.post(
            "/api/db/test/table/notes/_batch",
            json=batch_body,
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp1.status_code == 200
        assert resp1.json()["count"] == 2

        resp2 = client.post(
            "/api/db/test/table/notes/_batch",
            json=batch_body,
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert resp2.status_code == 200
        assert resp2.json() == resp1.json()
