"""Tests for the plugin system."""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from dataportal.plugins.base import (
    BasePlugin, OutputPlugin, HTMLBlockPlugin, PagePlugin, SQLFilterPlugin,
    PluginMeta, PluginState,
)
from dataportal.plugins.discovery import discover_plugins, PluginCandidate
from dataportal.plugins.compat import check_compatibility, CompatResult
from dataportal.plugins.manager import PluginManager, PluginEntry
from dataportal.plugins.signing import generate_keypair, sign_package, verify_signature
from dataportal.plugins.sandbox import PluginImportBlocker, SandboxContext


# --- Test fixtures ---

class MockOutputPlugin(OutputPlugin):
    meta = PluginMeta(
        name="mock-output",
        version="1.0.0",
        dataportal_version=">=2.0.0",
        author="Test",
        description="Mock output plugin",
    )

    async def initialize(self, ctx):
        pass

    async def health_check(self):
        return {"status": "healthy"}

    async def shutdown(self):
        pass

    def format_name(self):
        return "mock"

    def content_type(self):
        return "text/plain"

    def render(self, columns, rows, metadata):
        return b"mock output"


class FailingPlugin(BasePlugin):
    meta = PluginMeta(
        name="failing",
        version="1.0.0",
        dataportal_version=">=2.0.0",
    )

    async def initialize(self, ctx):
        raise RuntimeError("Plugin initialization failed!")

    async def health_check(self):
        return {"status": "unhealthy"}

    async def shutdown(self):
        pass


class MockSQLFilter(SQLFilterPlugin):
    meta = PluginMeta(
        name="mock-filter",
        version="1.0.0",
        dataportal_version=">=2.0.0",
    )

    async def initialize(self, ctx):
        pass

    async def health_check(self):
        return {"status": "healthy"}

    async def shutdown(self):
        pass

    def pre_process(self, sql, context):
        return sql + " /* filtered */"

    def post_process(self, columns, rows, context):
        return columns, rows


# --- Discovery tests ---

class TestPluginDiscovery:
    def test_discover_builtin_plugins(self):
        candidates = discover_plugins()
        names = [c.name for c in candidates]
        assert "format-json" in names
        assert "format-csv" in names
        assert "format-xml" in names
        assert "sql-filter" in names

    def test_candidate_has_meta(self):
        candidates = discover_plugins()
        for c in candidates:
            assert c.meta is not None
            assert c.meta.version == "1.0.0"
            assert c.plugin_class is not None
            assert c.error is None

    def test_candidate_loads_class(self):
        candidates = discover_plugins()
        json_plugin = next(c for c in candidates if c.name == "format-json")
        assert json_plugin.plugin_class is not None
        assert issubclass(json_plugin.plugin_class, OutputPlugin)


# --- Compatibility tests ---

class TestPluginCompat:
    def test_compatible_plugin(self):
        meta = PluginMeta(
            name="test",
            version="1.0.0",
            dataportal_version=">=2.0.0",
            python_version=">=3.10",
        )
        result = check_compatibility(meta, [])
        assert result.compatible is True
        assert result.reasons == []

    def test_incompatible_dataportal_version(self):
        meta = PluginMeta(
            name="test",
            version="1.0.0",
            dataportal_version=">=99.0.0",
        )
        result = check_compatibility(meta, [])
        assert result.compatible is False
        assert any("dataportal" in r for r in result.reasons)

    def test_incompatible_python_version(self):
        meta = PluginMeta(
            name="test",
            version="1.0.0",
            python_version=">=99.0",
        )
        result = check_compatibility(meta, [])
        assert result.compatible is False
        assert any("Python" in r for r in result.reasons)

    def test_conflict_detection(self):
        meta = PluginMeta(
            name="test",
            version="1.0.0",
            conflicts_with=["other-plugin"],
        )
        result = check_compatibility(meta, ["other-plugin"])
        assert result.compatible is False
        assert any("Conflicts" in r for r in result.reasons)

    def test_no_conflict_when_not_loaded(self):
        meta = PluginMeta(
            name="test",
            version="1.0.0",
            conflicts_with=["other-plugin"],
        )
        result = check_compatibility(meta, ["different-plugin"])
        assert result.compatible is True


# --- Plugin Manager tests ---

class TestPluginManager:
    @pytest.fixture
    def manager(self):
        return PluginManager()

    @pytest.fixture
    def mock_ctx(self):
        ctx = MagicMock()
        ctx.config.get.return_value = {}
        return ctx

    @pytest.mark.asyncio
    async def test_discover_finds_builtin(self, manager):
        candidates = await manager.discover()
        assert len(candidates) >= 4

    @pytest.mark.asyncio
    async def test_initialize_all(self, manager, mock_ctx):
        await manager.initialize_all(mock_ctx)
        plugins = manager.list_plugins()
        assert len(plugins) >= 4
        for p in plugins:
            assert p["state"] in ("initialized", "healthy")

    @pytest.mark.asyncio
    async def test_output_plugin_registered(self, manager, mock_ctx):
        await manager.initialize_all(mock_ctx)
        formats = manager.get_output_formats()
        assert "json" in formats
        assert "csv" in formats
        assert "xml" in formats

    @pytest.mark.asyncio
    async def test_get_output_plugin(self, manager, mock_ctx):
        await manager.initialize_all(mock_ctx)
        csv_plugin = manager.get_output_plugin("csv")
        assert csv_plugin is not None
        assert csv_plugin.format_name() == "csv"

    @pytest.mark.asyncio
    async def test_unload_plugin(self, manager, mock_ctx):
        await manager.initialize_all(mock_ctx)
        assert manager.get_output_plugin("csv") is not None
        await manager.unload("format-csv")
        assert manager.get_output_plugin("csv") is None

    @pytest.mark.asyncio
    async def test_disable_plugin(self, manager, mock_ctx):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("{}")
            state_path = Path(f.name)

        manager.set_state_file(state_path)
        await manager.discover()
        await manager.disable("format-xml")

        plugins = manager.list_plugins()
        xml = next(p for p in plugins if p["name"] == "format-xml")
        assert xml["state"] == "disabled"

        state_path.unlink()

    @pytest.mark.asyncio
    async def test_health_check(self, manager, mock_ctx):
        await manager.initialize_all(mock_ctx)
        result = await manager.health_check("format-json")
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_failure_rollback(self, manager, mock_ctx):
        """Plugins that fail to initialize should not register their types."""
        candidate = PluginCandidate(
            name="failing",
            entry_point=None,
            module_path="test.failing",
        )
        candidate.plugin_class = FailingPlugin
        candidate.meta = FailingPlugin.meta

        manager._plugins["failing"] = PluginEntry(candidate)
        await manager.load("failing")
        result = await manager.initialize("failing", mock_ctx)
        assert result is False
        info = manager.get_plugin_info("failing")
        assert info["state"] == "failed"


# --- Signing tests ---

class TestPluginSigning:
    def test_generate_keypair(self):
        private, public = generate_keypair()
        assert len(private) > 0
        assert len(public) > 0
        assert private != public

    def test_sign_and_verify(self, tmp_path):
        private, public = generate_keypair()

        # Create a test file
        test_file = tmp_path / "test_package.whl"
        test_file.write_bytes(b"fake package content")

        signature = sign_package(str(test_file), private)
        result = verify_signature(str(test_file), signature, public)
        assert result.valid is True

    def test_invalid_signature(self, tmp_path):
        _, public = generate_keypair()

        test_file = tmp_path / "test_package.whl"
        test_file.write_bytes(b"fake package content")

        import base64
        fake_sig = base64.b64encode(b"x" * 64).decode()
        result = verify_signature(str(test_file), fake_sig, public)
        assert result.valid is False

    def test_tampered_file(self, tmp_path):
        private, public = generate_keypair()

        test_file = tmp_path / "test_package.whl"
        test_file.write_bytes(b"original content")

        signature = sign_package(str(test_file), private)

        # Tamper with file
        test_file.write_bytes(b"tampered content")
        result = verify_signature(str(test_file), signature, public)
        assert result.valid is False


# --- Sandbox tests ---

class TestPluginSandbox:
    def test_blocks_uncached_dangerous_module(self):
        """Test that the blocker prevents fresh imports of blocked modules."""
        import sys
        # Remove pty from cache if present, since it's rarely pre-imported
        was_cached = "pty" in sys.modules
        cached_mod = sys.modules.pop("pty", None)
        try:
            blocker = PluginImportBlocker()
            blocker.activate()
            try:
                with pytest.raises(ImportError, match="sandbox"):
                    __import__("pty")
            finally:
                blocker.deactivate()
        finally:
            if was_cached and cached_mod:
                sys.modules["pty"] = cached_mod

    def test_allows_safe_imports(self):
        with SandboxContext():
            import json  # noqa
            import os  # noqa
            import hashlib  # noqa

    def test_blocks_ctypes_uncached(self):
        import sys
        was_cached = "ctypes" in sys.modules
        cached_mod = sys.modules.pop("ctypes", None)
        # Also remove submodules
        cached_subs = {k: v for k, v in sys.modules.items() if k.startswith("ctypes.")}
        for k in cached_subs:
            del sys.modules[k]
        try:
            with SandboxContext():
                with pytest.raises(ImportError, match="sandbox"):
                    __import__("ctypes")
        finally:
            if was_cached and cached_mod:
                sys.modules["ctypes"] = cached_mod
                sys.modules.update(cached_subs)

    def test_deactivates_after_context(self):
        with SandboxContext():
            pass
        # Should work fine outside context
        import subprocess  # noqa


# --- Output Plugin tests ---

class TestOutputPlugins:
    def test_json_plugin(self):
        from dataportal.plugins.builtin.format_json import JSONOutputPlugin
        plugin = JSONOutputPlugin()
        data = plugin.render(["id", "name"], [[1, "test"], [2, "test2"]], {"db": "test"})
        parsed = json.loads(data)
        assert parsed["columns"] == ["id", "name"]
        assert parsed["row_count"] == 2

    def test_csv_plugin(self):
        from dataportal.plugins.builtin.format_csv import CSVOutputPlugin
        plugin = CSVOutputPlugin()
        data = plugin.render(["id", "name"], [[1, "test"], [2, "test2"]], {})
        text = data.decode("utf-8")
        lines = text.strip().split("\r\n")
        assert lines[0] == "id,name"
        assert lines[1] == "1,test"

    def test_xml_plugin(self):
        from dataportal.plugins.builtin.format_xml import XMLOutputPlugin
        plugin = XMLOutputPlugin()
        data = plugin.render(["id", "name"], [[1, "test"]], {"db": "test"})
        text = data.decode("utf-8")
        assert "<result>" in text
        assert '<column name="id"' in text
        assert "<cell" in text


# --- SQL Filter Plugin tests ---

class TestSQLFilterPlugin:
    @pytest.fixture
    def filter_plugin(self):
        from dataportal.plugins.builtin.sql_filter import ASTSQLFilterPlugin
        plugin = ASTSQLFilterPlugin()
        plugin._row_filters = [
            {"table": "users", "condition": "city = '北京'"}
        ]
        plugin._column_masks = [
            {"table": "users", "column": "email", "strategy": "partial"}
        ]
        return plugin

    def test_pre_process_injects_where(self, filter_plugin):
        sql = 'SELECT * FROM users'
        result = filter_plugin.pre_process(sql, {})
        assert "city" in result.lower()
        assert "北京" in result

    def test_pre_process_adds_to_existing_where(self, filter_plugin):
        sql = "SELECT * FROM users WHERE age > 18"
        result = filter_plugin.pre_process(sql, {})
        assert "age" in result.lower()
        assert "city" in result.lower()

    def test_pre_process_no_match(self, filter_plugin):
        sql = "SELECT * FROM orders"
        result = filter_plugin.pre_process(sql, {})
        # orders table has no filter, so the WHERE should NOT be added
        assert "city" not in result.lower()

    def test_post_process_masks_email(self, filter_plugin):
        columns = ["id", "name", "email"]
        rows = [[1, "Test", "user@example.com"], [2, "Test2", "other@domain.org"]]
        _, masked_rows = filter_plugin.post_process(columns, rows, {})
        assert "***@example.com" in masked_rows[0][2]
        assert "***@domain.org" in masked_rows[1][2]

    def test_post_process_null_values(self, filter_plugin):
        columns = ["id", "name", "email"]
        rows = [[1, "Test", None]]
        _, masked_rows = filter_plugin.post_process(columns, rows, {})
        assert masked_rows[0][2] is None

    def test_mask_strategies(self):
        from dataportal.plugins.builtin.sql_filter import ASTSQLFilterPlugin
        plugin = ASTSQLFilterPlugin()

        assert plugin._apply_mask("test@example.com", {"strategy": "full"}) == "***"
        assert "***" in plugin._apply_mask("test@example.com", {"strategy": "partial"})
        assert len(plugin._apply_mask("test", {"strategy": "hash"})) == 16
        assert plugin._apply_mask("test", {"strategy": "null"}) == ""
