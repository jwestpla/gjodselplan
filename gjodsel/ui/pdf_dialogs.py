"""Save-as dialogs for the PDF exports (reportlab is imported only when exporting)."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING

from ..attachments import open_path
from ..plan_view import PlanView
from .widgets import show_errors

if TYPE_CHECKING:
    from .main_window import MainWindow


def export_plan_pdf(parent, app: MainWindow, view: PlanView) -> None:
    _export(parent, app, view, "Gjodslingsplan")


def export_journal_pdf(parent, app: MainWindow, view: PlanView) -> None:
    _export(parent, app, view, "Gjodseljournal")


def _export(parent, app: MainWindow, view: PlanView, kind: str) -> None:
    try:
        from .. import pdf_export
    except ImportError:
        show_errors(parent, ["PDF-eksport krever reportlab. Installer med: pip install -r requirements.txt"],
                    "PDF")
        return
    if not view.finished and kind == "Gjodslingsplan" and not messagebox.askyesno(
        "Utkast", "Planen er ikke ferdigstilt. Eksportere utkastet likevel?", parent=parent,
    ):
        return
    app.paths.export_dir.mkdir(parents=True, exist_ok=True)
    filename = filedialog.asksaveasfilename(
        parent=parent, title="Lagre PDF", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
        initialdir=str(app.paths.export_dir), initialfile=pdf_export.default_filename(view, kind),
    )
    if not filename:
        return
    try:
        if kind == "Gjodslingsplan":
            path = pdf_export.export_plan(view, Path(filename), app.paths.data_dir)
        else:
            path = pdf_export.export_journal(view, Path(filename))
    except OSError as exc:
        show_errors(parent, [f"Kunne ikke lagre PDF: {exc}"], "PDF")
        return
    app.set_status(f"PDF lagret: {path}")
    if messagebox.askyesno("PDF", f"PDF lagret:\n{path}\n\nÅpne den nå?", parent=parent):
        open_path(path)
