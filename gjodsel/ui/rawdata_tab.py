"""Rådata: read-only view of any database table, and CSV export of all tables."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import repo
from ..attachments import open_path
from ..csv_export import export_all
from ..units import format_plain
from .widgets import configure_columns, scrolled_tree

if TYPE_CHECKING:
    from .main_window import MainWindow

_MAX_CELL = 120


class RawDataTab(ttk.Frame):
    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=8)
        self.app = app
        self.table_var = tk.StringVar()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Tabell").pack(side="left")
        self.table_combo = ttk.Combobox(top, textvariable=self.table_var, state="readonly", width=24)
        self.table_combo.pack(side="left", padx=6)
        self.table_combo.bind("<<ComboboxSelected>>", lambda _e: self._load_table())
        ttk.Button(top, text="Oppdater", command=self.refresh).pack(side="left")
        ttk.Button(top, text="Eksporter alle til CSV", command=self.export).pack(side="left", padx=6)
        self.count_label = ttk.Label(top, text="")
        self.count_label.pack(side="right")

        frame, self.tree = scrolled_tree(self, [], height=20, xscroll=True)
        frame.grid(row=1, column=0, sticky="nsew")
        ttk.Label(
            self, text="Kun visning. Databasen kan også åpnes direkte med DB Browser for SQLite.",
            foreground="#666666",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

    def refresh(self) -> None:
        tables = repo.list_tables(self.app.conn)
        self.table_combo["values"] = tables
        if self.table_var.get() not in tables:
            self.table_var.set("enterprise" if "enterprise" in tables else (tables[0] if tables else ""))
        self._load_table()

    def _load_table(self) -> None:
        table = self.table_var.get()
        self.tree.delete(*self.tree.get_children())
        if not table:
            return
        columns, rows = repo.read_table(self.app.conn, table)
        configure_columns(self.tree, [(c, c, max(60, min(len(c) * 9, 220))) for c in columns])
        for row in rows:
            self.tree.insert("", "end", values=[_display(v) for v in row])
        self.count_label.configure(text=f"{len(rows)} rader")

    def export(self) -> None:
        folder = export_all(self.app.conn, self.app.paths.export_dir)
        n = len(list(folder.glob("*.csv")))
        if messagebox.askyesno(
            "Eksportert", f"{n} tabeller er eksportert til\n{folder}\n\nÅpne mappen?", parent=self
        ):
            open_path(folder)


def _display(value) -> str:
    if value is None:
        return ""
    text = format_plain(value) if isinstance(value, float) else str(value)
    text = text.replace("\n", " ⏎ ")
    return text if len(text) <= _MAX_CELL else text[:_MAX_CELL] + "…"
