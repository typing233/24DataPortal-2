"""Async CSV importer with incremental import, type inference, and encoding detection."""
import asyncio
import csv
import hashlib
import io
import os
import re
import time
from pathlib import Path
from typing import Any

import aiosqlite
import chardet


class ImportStatus:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.status: str = "pending"
        self.progress: float = 0.0
        self.rows_imported: int = 0
        self.total_rows: int = 0
        self.error: str | None = None
        self.started_at: float = 0
        self.finished_at: float = 0
        self.table_name: str = ""
        self.file_hash: str = ""


class CSVImporter:
    def __init__(self, config: dict):
        self._config = config
        self._import_status: dict[str, ImportStatus] = {}
        self._imported_hashes: dict[str, set[str]] = {}

    @property
    def status_map(self) -> dict[str, ImportStatus]:
        return self._import_status

    def _detect_encoding(self, file_path: str) -> str:
        with open(file_path, "rb") as f:
            raw = f.read(min(100000, os.path.getsize(file_path)))
        result = chardet.detect(raw)
        return result.get("encoding") or "utf-8"

    def _compute_file_hash(self, file_path: str) -> str:
        h = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def _infer_column_type(self, values: list[str]) -> str:
        sample = [v for v in values[:200] if v.strip()]
        if not sample:
            return "TEXT"

        int_count = 0
        float_count = 0
        for v in sample:
            v = v.strip()
            try:
                int(v)
                int_count += 1
                continue
            except ValueError:
                pass
            try:
                float(v)
                float_count += 1
            except ValueError:
                pass

        ratio = len(sample)
        if int_count / ratio > 0.8:
            return "INTEGER"
        if (int_count + float_count) / ratio > 0.8:
            return "REAL"
        return "TEXT"

    def _sanitize_table_name(self, filename: str) -> str:
        name = Path(filename).stem
        name = re.sub(r"[^\w]", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        if name[0:1].isdigit():
            name = "t_" + name
        return name.lower()

    async def import_csv(
        self, file_path: str, db_path: str, db_name: str
    ) -> ImportStatus:
        status = ImportStatus(file_path)
        self._import_status[file_path] = status
        status.started_at = time.time()
        status.status = "running"

        try:
            file_hash = self._compute_file_hash(file_path)
            status.file_hash = file_hash

            if db_name not in self._imported_hashes:
                self._imported_hashes[db_name] = set()
            if file_hash in self._imported_hashes[db_name]:
                status.status = "skipped"
                status.error = "File already imported (duplicate hash)"
                status.finished_at = time.time()
                return status

            max_size = self._config.get("max_file_size_mb", 500) * 1024 * 1024
            if os.path.getsize(file_path) > max_size:
                status.status = "error"
                status.error = f"File exceeds max size ({max_size // (1024*1024)}MB)"
                status.finished_at = time.time()
                return status

            encoding = "utf-8"
            if self._config.get("encoding_detection", True):
                encoding = self._detect_encoding(file_path)

            table_name = self._sanitize_table_name(file_path)
            status.table_name = table_name

            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                status.total_rows = sum(1 for _ in f) - 1

            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                reader = csv.reader(f)
                headers = next(reader)
                headers = [re.sub(r"[^\w]", "_", h.strip()).lower() or f"col_{i}" for i, h in enumerate(headers)]

                sample_rows = []
                for i, row in enumerate(reader):
                    if i >= 200:
                        break
                    sample_rows.append(row)

            col_types = []
            for col_idx in range(len(headers)):
                values = [r[col_idx] for r in sample_rows if col_idx < len(r)]
                col_types.append(self._infer_column_type(values))

            async with aiosqlite.connect(db_path) as conn:
                await conn.execute("PRAGMA journal_mode=WAL")

                cols_def = ", ".join(
                    f'"{h}" {t}' for h, t in zip(headers, col_types)
                )
                await conn.execute(
                    f'CREATE TABLE IF NOT EXISTS "{table_name}" ({cols_def})'
                )

                chunk_size = self._config.get("chunk_size", 5000)
                placeholders = ", ".join(["?"] * len(headers))
                insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'

                with open(file_path, "r", encoding=encoding, errors="replace") as f:
                    reader = csv.reader(f)
                    next(reader)
                    batch = []
                    for row in reader:
                        if len(row) < len(headers):
                            row.extend([""] * (len(headers) - len(row)))
                        elif len(row) > len(headers):
                            row = row[: len(headers)]
                        batch.append(row)
                        if len(batch) >= chunk_size:
                            await conn.executemany(insert_sql, batch)
                            await conn.commit()
                            status.rows_imported += len(batch)
                            status.progress = (
                                status.rows_imported / max(status.total_rows, 1)
                            )
                            batch = []
                            await asyncio.sleep(0)

                    if batch:
                        await conn.executemany(insert_sql, batch)
                        await conn.commit()
                        status.rows_imported += len(batch)

                if self._config.get("create_indexes", True):
                    for i, (h, t) in enumerate(zip(headers, col_types)):
                        if t == "INTEGER" or i == 0:
                            idx_name = f"idx_{table_name}_{h}"
                            await conn.execute(
                                f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{table_name}"("{h}")'
                            )
                    await conn.commit()

                # Record import metadata
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS _dataportal_imports (
                        file_path TEXT, file_hash TEXT, table_name TEXT,
                        imported_at REAL, row_count INTEGER,
                        PRIMARY KEY(file_hash)
                    )
                """)
                await conn.execute(
                    "INSERT OR REPLACE INTO _dataportal_imports VALUES (?,?,?,?,?)",
                    (file_path, file_hash, table_name, time.time(), status.rows_imported),
                )
                await conn.commit()

            self._imported_hashes[db_name].add(file_hash)
            status.status = "completed"
            status.progress = 1.0

        except Exception as e:
            status.status = "error"
            status.error = str(e)

        status.finished_at = time.time()
        return status

    async def load_existing_hashes(self, db_path: str, db_name: str):
        if db_name not in self._imported_hashes:
            self._imported_hashes[db_name] = set()
        try:
            async with aiosqlite.connect(db_path) as conn:
                cursor = await conn.execute(
                    "SELECT file_hash FROM _dataportal_imports"
                )
                rows = await cursor.fetchall()
                for row in rows:
                    self._imported_hashes[db_name].add(row[0])
        except Exception:
            pass
