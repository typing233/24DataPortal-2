"""XML output format plugin."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from dataportal.plugins.base import OutputPlugin, PluginMeta


class XMLOutputPlugin(OutputPlugin):
    meta = PluginMeta(
        name="format-xml",
        version="1.0.0",
        dataportal_version=">=2.0.0",
        author="DataPortal",
        description="XML output format for query results",
    )

    async def initialize(self, ctx: Any) -> None:
        pass

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy"}

    async def shutdown(self) -> None:
        pass

    def format_name(self) -> str:
        return "xml"

    def content_type(self) -> str:
        return "application/xml; charset=utf-8"

    def render(self, columns: list[str], rows: list[list], metadata: dict) -> bytes:
        root = ET.Element("result")

        meta_el = ET.SubElement(root, "metadata")
        for key, value in metadata.items():
            el = ET.SubElement(meta_el, key)
            el.text = str(value)

        columns_el = ET.SubElement(root, "columns")
        for col in columns:
            el = ET.SubElement(columns_el, "column")
            el.set("name", col)

        rows_el = ET.SubElement(root, "rows")
        rows_el.set("count", str(len(rows)))
        for row in rows:
            row_el = ET.SubElement(rows_el, "row")
            for i, value in enumerate(row):
                cell = ET.SubElement(row_el, "cell")
                cell.set("column", columns[i] if i < len(columns) else f"col{i}")
                cell.text = str(value) if value is not None else ""

        return ET.tostring(root, encoding="unicode", xml_declaration=True).encode("utf-8")
