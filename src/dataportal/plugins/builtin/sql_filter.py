"""SQL filter plugin - AST-based permission filtering and data masking."""
from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

from dataportal.plugins.base import SQLFilterPlugin, PluginMeta


class ASTSQLFilterPlugin(SQLFilterPlugin):
    meta = PluginMeta(
        name="sql-filter",
        version="1.0.0",
        dataportal_version=">=2.0.0",
        author="DataPortal",
        description="AST-based SQL permission filtering and data masking/desensitization",
    )

    def __init__(self):
        self._row_filters: list[dict] = []
        self._column_masks: list[dict] = []
        self._config: dict = {}

    async def initialize(self, ctx: Any) -> None:
        self._config = ctx.config.get("sql_filter", default={}) or {}
        self._row_filters = self._config.get("row_filters", [])
        self._column_masks = self._config.get("column_masks", [])

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "row_filters": len(self._row_filters),
            "column_masks": len(self._column_masks),
        }

    async def shutdown(self) -> None:
        pass

    def pre_process(self, sql: str, context: dict) -> str:
        """Inject WHERE clauses based on row-level security rules."""
        if not self._row_filters:
            return sql

        try:
            parsed = sqlglot.parse_one(sql, dialect="sqlite")
        except Exception:
            return sql

        for rule in self._row_filters:
            table_name = rule.get("table", "")
            condition = rule.get("condition", "")
            if not table_name or not condition:
                continue

            tables_in_query = self._find_tables(parsed)
            if table_name.lower() in [t.lower() for t in tables_in_query]:
                condition_expr = sqlglot.parse_one(
                    f"SELECT 1 WHERE {condition}", dialect="sqlite"
                ).find(exp.Where).this

                existing_where = parsed.find(exp.Where)
                if existing_where:
                    new_condition = exp.And(
                        this=existing_where.this, expression=condition_expr
                    )
                    existing_where.set("this", new_condition)
                else:
                    parsed.set("where", exp.Where(this=condition_expr))

        return parsed.sql(dialect="sqlite")

    def post_process(
        self, columns: list[str], rows: list[list], context: dict
    ) -> tuple[list[str], list[list]]:
        """Apply data masking/desensitization to result columns."""
        if not self._column_masks:
            return columns, rows

        mask_map: dict[str, dict] = {}
        for rule in self._column_masks:
            col_name = rule.get("column", "").lower()
            if col_name:
                mask_map[col_name] = rule

        col_indices = []
        for i, col in enumerate(columns):
            if col.lower() in mask_map:
                col_indices.append((i, mask_map[col.lower()]))

        if not col_indices:
            return columns, rows

        masked_rows = []
        for row in rows:
            new_row = list(row)
            for idx, rule in col_indices:
                if new_row[idx] is not None:
                    new_row[idx] = self._apply_mask(str(new_row[idx]), rule)
            masked_rows.append(new_row)

        return columns, masked_rows

    def _apply_mask(self, value: str, rule: dict) -> str:
        strategy = rule.get("strategy", "full")

        if strategy == "full":
            return "***"
        elif strategy == "partial":
            if "@" in value:
                local, domain = value.rsplit("@", 1)
                return f"{local[:2]}***@{domain}"
            elif len(value) > 4:
                return f"***{value[-4:]}"
            else:
                return "***"
        elif strategy == "hash":
            import hashlib
            return hashlib.sha256(value.encode()).hexdigest()[:16]
        elif strategy == "null":
            return ""
        else:
            return "***"

    def _find_tables(self, parsed) -> list[str]:
        """Extract all table names from a parsed SQL expression."""
        tables = []
        for table in parsed.find_all(exp.Table):
            if table.name:
                tables.append(table.name)
        return tables
