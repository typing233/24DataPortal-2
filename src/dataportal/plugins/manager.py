"""Plugin lifecycle manager - discover, validate, load, initialize, health-check, unload."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dataportal.plugins.base import (
    BasePlugin,
    OutputPlugin,
    HTMLBlockPlugin,
    PagePlugin,
    SQLFilterPlugin,
    PluginState,
    PluginMeta,
)
from dataportal.plugins.discovery import discover_plugins, PluginCandidate
from dataportal.plugins.compat import check_compatibility
from dataportal.plugins.sandbox import run_sandboxed_async

if TYPE_CHECKING:
    from dataportal.context import AppContext

logger = logging.getLogger(__name__)


class PluginEntry:
    def __init__(self, candidate: PluginCandidate):
        self.name = candidate.name
        self.candidate = candidate
        self.instance: BasePlugin | None = None
        self.state: PluginState = PluginState.DISCOVERED
        self.error: str | None = candidate.error
        self.loaded_at: float | None = None
        self.health: dict[str, Any] = {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "error": self.error,
            "loaded_at": self.loaded_at,
            "health": self.health,
            "meta": {
                "version": self.candidate.meta.version if self.candidate.meta else "unknown",
                "author": self.candidate.meta.author if self.candidate.meta else "",
                "description": self.candidate.meta.description if self.candidate.meta else "",
                "dataportal_version": self.candidate.meta.dataportal_version if self.candidate.meta else "",
            },
        }


class PluginManager:
    def __init__(self):
        self._plugins: dict[str, PluginEntry] = {}
        self._output_plugins: dict[str, OutputPlugin] = {}
        self._html_block_plugins: dict[str, list[HTMLBlockPlugin]] = {}
        self._page_plugins: list[PagePlugin] = []
        self._sql_filter_plugins: list[SQLFilterPlugin] = []
        self._disabled: set[str] = set()
        self._state_file: Path | None = None

    def set_state_file(self, path: Path):
        self._state_file = path
        self._load_state()

    def _load_state(self):
        if self._state_file and self._state_file.exists():
            data = json.loads(self._state_file.read_text())
            self._disabled = set(data.get("disabled", []))

    def _save_state(self):
        if self._state_file:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps({
                "disabled": list(self._disabled),
            }, indent=2))

    async def discover(self) -> list[PluginCandidate]:
        candidates = discover_plugins()
        for c in candidates:
            if c.name not in self._plugins:
                self._plugins[c.name] = PluginEntry(c)
        return candidates

    async def validate(self, name: str) -> tuple[bool, list[str]]:
        entry = self._plugins.get(name)
        if not entry:
            return False, [f"Plugin '{name}' not found"]
        if entry.error:
            return False, [entry.error]
        if not entry.candidate.meta:
            return False, ["Plugin has no metadata"]

        loaded_names = [
            n for n, e in self._plugins.items()
            if e.state in (PluginState.INITIALIZED, PluginState.HEALTHY)
        ]
        result = check_compatibility(entry.candidate.meta, loaded_names)
        if result.compatible:
            entry.state = PluginState.VALIDATED
        return result.compatible, result.reasons

    async def load(self, name: str) -> BasePlugin | None:
        entry = self._plugins.get(name)
        if not entry or not entry.candidate.plugin_class:
            return None

        if name in self._disabled:
            entry.state = PluginState.DISABLED
            return None

        try:
            instance = entry.candidate.plugin_class()
            entry.instance = instance
            entry.state = PluginState.LOADED
            entry.loaded_at = time.time()
            return instance
        except Exception as e:
            entry.state = PluginState.FAILED
            entry.error = f"Instantiation failed: {e}"
            logger.error(f"Plugin '{name}' failed to load: {e}")
            return None

    async def initialize(self, name: str, ctx: AppContext) -> bool:
        entry = self._plugins.get(name)
        if not entry or not entry.instance:
            return False

        try:
            permissions = entry.candidate.meta.permissions if entry.candidate.meta else []
            await run_sandboxed_async(
                entry.instance.initialize, ctx, permissions=permissions
            )
            entry.state = PluginState.INITIALIZED
            self._register_plugin_type(name, entry.instance)
            return True
        except Exception as e:
            self._unregister_plugin_type(name, entry.instance)
            entry.state = PluginState.FAILED
            entry.error = f"Initialization failed: {e}"
            logger.error(f"Plugin '{name}' failed to initialize: {e}")
            return False

    async def health_check(self, name: str) -> dict[str, Any]:
        entry = self._plugins.get(name)
        if not entry or not entry.instance:
            return {"status": "not_loaded"}

        try:
            result = await entry.instance.health_check()
            entry.health = result
            entry.state = PluginState.HEALTHY
            return result
        except Exception as e:
            entry.state = PluginState.UNHEALTHY
            entry.health = {"status": "unhealthy", "error": str(e)}
            return entry.health

    async def unload(self, name: str) -> bool:
        entry = self._plugins.get(name)
        if not entry or not entry.instance:
            return False

        try:
            await entry.instance.shutdown()
        except Exception as e:
            logger.warning(f"Plugin '{name}' shutdown error: {e}")

        self._unregister_plugin_type(name, entry.instance)
        entry.instance = None
        entry.state = PluginState.UNLOADED
        return True

    async def reload(self, name: str, ctx: AppContext) -> bool:
        await self.unload(name)
        instance = await self.load(name)
        if not instance:
            return False
        return await self.initialize(name, ctx)

    async def enable(self, name: str):
        self._disabled.discard(name)
        self._save_state()
        entry = self._plugins.get(name)
        if entry and entry.state == PluginState.DISABLED:
            entry.state = PluginState.DISCOVERED

    async def disable(self, name: str):
        self._disabled.add(name)
        self._save_state()
        if name in self._plugins:
            await self.unload(name)
            self._plugins[name].state = PluginState.DISABLED

    async def initialize_all(self, ctx: AppContext):
        """Discover and initialize all available plugins."""
        await self.discover()
        for name, entry in list(self._plugins.items()):
            if name in self._disabled:
                entry.state = PluginState.DISABLED
                continue
            if entry.error:
                continue
            valid, reasons = await self.validate(name)
            if not valid:
                logger.warning(f"Plugin '{name}' incompatible: {reasons}")
                continue
            instance = await self.load(name)
            if instance:
                await self.initialize(name, ctx)

    def _register_plugin_type(self, name: str, plugin: BasePlugin):
        if isinstance(plugin, OutputPlugin):
            self._output_plugins[plugin.format_name()] = plugin
        if isinstance(plugin, HTMLBlockPlugin):
            point = plugin.injection_point()
            self._html_block_plugins.setdefault(point, []).append(plugin)
        if isinstance(plugin, PagePlugin):
            self._page_plugins.append(plugin)
        if isinstance(plugin, SQLFilterPlugin):
            self._sql_filter_plugins.append(plugin)

    def _unregister_plugin_type(self, name: str, plugin: BasePlugin):
        if isinstance(plugin, OutputPlugin):
            fmt = plugin.format_name()
            self._output_plugins.pop(fmt, None)
        if isinstance(plugin, HTMLBlockPlugin):
            point = plugin.injection_point()
            plugins = self._html_block_plugins.get(point, [])
            self._html_block_plugins[point] = [p for p in plugins if p is not plugin]
        if isinstance(plugin, PagePlugin):
            self._page_plugins = [p for p in self._page_plugins if p is not plugin]
        if isinstance(plugin, SQLFilterPlugin):
            self._sql_filter_plugins = [p for p in self._sql_filter_plugins if p is not plugin]

    def get_output_plugin(self, format_name: str) -> OutputPlugin | None:
        return self._output_plugins.get(format_name)

    def get_output_formats(self) -> list[str]:
        return list(self._output_plugins.keys())

    async def render_blocks(self, injection_point: str, request: Any = None, context: dict | None = None) -> str:
        plugins = self._html_block_plugins.get(injection_point, [])
        parts = []
        for plugin in plugins:
            try:
                html = await plugin.render_block(request, context or {})
                parts.append(html)
            except Exception as e:
                logger.error(f"HTMLBlockPlugin error at '{injection_point}': {e}")
        return "\n".join(parts)

    def get_page_routes(self) -> list:
        routes = []
        for plugin in self._page_plugins:
            try:
                routes.extend(plugin.get_routes())
            except Exception as e:
                logger.error(f"PagePlugin route error: {e}")
        return routes

    def get_nav_items(self) -> list[dict]:
        items = []
        for plugin in self._page_plugins:
            try:
                items.extend(plugin.nav_items())
            except Exception as e:
                logger.error(f"PagePlugin nav error: {e}")
        return items

    def get_sql_filters(self) -> list[SQLFilterPlugin]:
        return self._sql_filter_plugins

    def list_plugins(self) -> list[dict]:
        return [entry.to_dict() for entry in self._plugins.values()]

    def get_plugin_info(self, name: str) -> dict | None:
        entry = self._plugins.get(name)
        return entry.to_dict() if entry else None
