"""CSV output format plugin."""
from __future__ import annotations

import csv
import io
from typing import Any

from dataportal.plugins.base import OutputPlugin, PluginMeta


class CSVOutputPlugin(OutputPlugin):
    meta = PluginMeta(
        name="format-csv",
        version="1.0.0",
        dataportal_version=">=2.0.0",
        author="DataPortal",
        description="CSV output format for query results",
    )

    async def initialize(self, ctx: Any) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy"}

    async def shutdown(self) -> None:
        pass

    def format_name(self) -> str:
        return "csv"

    def content_type(self) -> str:
        return "text/csv; charset=utf-8"

    def render(self, columns: list[str], rows: list[list], metadata: dict) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)
        return output.getvalue().encode("utf-8")
