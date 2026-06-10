"""Plugin discovery via setuptools entry points."""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from typing import Any

if sys.version_info >= (3, 12):
    from importlib.metadata import entry_points
else:
    from importlib.metadata import entry_points

from dataportal.plugins.base import BasePlugin, PluginMeta

ENTRY_POINT_GROUP = "dataportal.plugins"


@dataclass
class PluginCandidate:
    name: str
    entry_point: Any
    module_path: str
    plugin_class: type[BasePlugin] | None = None
    meta: PluginMeta | None = None
    error: str | None = None


def discover_plugins() -> list[PluginCandidate]:
    """Scan installed packages for dataportal.plugins entry points."""
    candidates = []
    eps = entry_points()

    if hasattr(eps, "select"):
        plugin_eps = eps.select(group=ENTRY_POINT_GROUP)
    else:
        plugin_eps = eps.get(ENTRY_POINT_GROUP, [])

    for ep in plugin_eps:
        candidate = PluginCandidate(
            name=ep.name,
            entry_point=ep,
            module_path=ep.value,
        )
        try:
            cls = ep.load()
            if not (isinstance(cls, type) and issubclass(cls, BasePlugin)):
                candidate.error = f"{ep.value} is not a BasePlugin subclass"
            else:
                candidate.plugin_class = cls
                if hasattr(cls, "meta") and isinstance(cls.meta, PluginMeta):
                    candidate.meta = cls.meta
                elif hasattr(cls, "get_meta"):
                    candidate.meta = cls.get_meta()
        except Exception as e:
            candidate.error = f"Failed to load: {e}"

        candidates.append(candidate)

    return candidates


def load_plugin_class(module_path: str) -> type[BasePlugin]:
    """Load a plugin class from a dotted module:ClassName path."""
    module_name, _, class_name = module_path.rpartition(":")
    if not module_name:
        module_name, _, class_name = module_path.rpartition(".")

    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)

    if not (isinstance(cls, type) and issubclass(cls, BasePlugin)):
        raise TypeError(f"{module_path} is not a BasePlugin subclass")

    return cls
