"""Export every database table to CSV for viewing in Excel.

UTF-8 with BOM and ';' separator, decimal comma: what Norwegian Excel expects,
so æøå and numbers come through correctly.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path

from . import repo
from .units import format_plain


def export_all(conn: sqlite3.Connection, export_dir: Path, today: date | None = None) -> Path:
    """Write <export_dir>/<YYYY-MM-DD>/<table>.csv for every table. Returns the folder."""
    target = export_dir / (today or date.today()).isoformat()
    target.mkdir(parents=True, exist_ok=True)
    for table in repo.list_tables(conn):
        columns, rows = repo.read_table(conn, table)
        with open(target / f"{table}.csv", "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(columns)
            writer.writerows([_cell(v) for v in row] for row in rows)
    return target


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return format_plain(value)
    return value
