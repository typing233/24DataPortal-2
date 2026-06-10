"""Application context container for runtime state."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dataportal.config import Config
    from dataportal.database import DatabaseRegistry
    from dataportal.cache import TTLCache
    from dataportal.sandbox import SQLSandbox
    from dataportal.importer import CSVImporter
    from dataportal.plugins.manager import PluginManager


@dataclass
class AppContext:
    config: Config
    registry: DatabaseRegistry
    cache: TTLCache
    sandbox: SQLSandbox
    importer: CSVImporter
    plugin_manager: PluginManager
    start_time: float = field(default_factory=time.time)
    saved_views: dict[str, list[dict]] = field(default_factory=dict)
