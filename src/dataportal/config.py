"""Configuration management with hot-reload support."""
import json
import os
import asyncio
import time
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "site": {
        "title": "DataPortal",
        "copyright": "© 2024 DataPortal",
        "description": "Interactive Data Exploration Portal",
    },
    "theme": {
        "primary_color": "#2563eb",
        "dark_mode": False,
    },
    "permissions": {
        "allow_sql_write": False,
        "allow_sql_ddl": False,
        "max_query_time_seconds": 30,
        "max_rows_return": 10000,
        "allowed_functions": [],
        "blocked_tables": [],
    },
    "import": {
        "strategy": "incremental",
        "chunk_size": 5000,
        "encoding_detection": True,
        "create_indexes": True,
        "max_file_size_mb": 500,
    },
    "cache": {
        "enabled": True,
        "ttl_seconds": 60,
        "max_entries": 1000,
    },
    "data_sources": [],
}


class Config:
    def __init__(self, config_path: str | None = None):
        self._config_path = config_path
        self._data: dict[str, Any] = {}
        self._last_mtime: float = 0
        self._lock = asyncio.Lock()
        self.load()

    def load(self):
        self._data = json.loads(json.dumps(DEFAULT_CONFIG))
        if self._config_path and Path(self._config_path).exists():
            with open(self._config_path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            self._deep_merge(self._data, user_config)
            self._last_mtime = Path(self._config_path).stat().st_mtime

    def _deep_merge(self, base: dict, override: dict):
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    async def check_reload(self) -> bool:
        if not self._config_path:
            return False
        path = Path(self._config_path)
        if not path.exists():
            return False
        mtime = path.stat().st_mtime
        if mtime > self._last_mtime:
            async with self._lock:
                self.load()
            return True
        return False

    def get(self, *keys: str, default: Any = None) -> Any:
        node = self._data
        for key in keys:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node

    @property
    def site(self) -> dict:
        return self._data.get("site", {})

    @property
    def theme(self) -> dict:
        return self._data.get("theme", {})

    @property
    def permissions(self) -> dict:
        return self._data.get("permissions", {})

    @property
    def import_config(self) -> dict:
        return self._data.get("import", {})

    @property
    def cache_config(self) -> dict:
        return self._data.get("cache", {})

    def to_dict(self) -> dict:
        return self._data.copy()
