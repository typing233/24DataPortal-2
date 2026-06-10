"""JSON output format plugin."""
from __future__ import annotations

import json
from typing import Any

from dataportal.plugins.base import OutputPlugin, PluginMeta


class JSONOutputPlugin(OutputPlugin):
    meta = PluginMeta(
        name="format-json",
        version="1.0.0",
        dataportal_version=">=2.0.0",
        author="DataPortal",
        description="JSON output format",
    )

    async def initialize(self, ctx: Any) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy"}

    async def shutdown(self) -> None:
        pass

    def format_name(self) -> str:
        return "json"

    def content_type(self) -> str:
        return "application/json"

    def render(self, columns: list[str], rows: list[list], metadata: dict) -> bytes:
        data = {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "metadata": metadata,
        }
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
