"""Main window: sidebar (enterprise + year), tab notebook and status bar.

Tabs implement refresh(). After any write, call data_changed(); the visible
tab refreshes at once and hidden tabs refresh when they are shown.
"""

from __future__ import annotations

import logging
import sqlite3
import tkinter as tk
from datetime import date
from tkinter import messagebox, simpledialog, ttk

from .. import __version__, plan_service, repo
from ..attachments import open_path
from ..config import Paths
from ..models import Settings
from ..reference import ReferenceDataError, RefTables, load_reference
from ..validation import ValidationError
from .enterprise_tab import EnterpriseTab
from .feedback_dialog import give_feedback, send_feedback
from .fields_tab import FieldsTab
from .journal_tab import JournalTab
from .plan_tab import PlanTab
from .rawdata_tab import RawDataTab
from .reference_tab import ReferenceTab
from .settings_tab import SettingsTab
from .soil_tab import SoilTab
from .widgets import AMBER, RED

_MAX_ERRORS_SHOWN = 25


class MainWindow(tk.Tk):
    def __init__(self, conn: sqlite3.Connection, paths: Paths,
                 ref: RefTables | None, ref_errors: list[str], first_start_notes: list[str] | None = None):
        super().__init__()
        self.conn = conn
        self.paths = paths
        self.ref = ref
        self.ref_errors = ref_errors
        self.enterprise_id: int | None = None
        self._enterprise_ids: list[int] = []
        self._stale: set[str] = set()

        self.title(f"Gjødselplan for frukt – versjon {__version__}")
        width = min(1200, self.winfo_screenwidth() - 40)
        height = min(760, self.winfo_screenheight() - 90)  # leave room for the Windows taskbar
        self.geometry(f"{width}x{height}+20+10")
        self.minsize(900, 560)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.search_var = tk.StringVar()
        self.year_var = tk.IntVar(value=date.today().year)

        self._build_menu()
        self._build_sidebar()
        self._build_tabs()
        self._build_statusbar()

        self.search_var.trace_add("write", lambda *_: self.reload_enterprises())
        self.year_var.trace_add("write", lambda *_: self.data_changed())
        enterprises = repo.list_enterprises(self.conn)
        if len(enterprises) == 1:  # e.g. the demo: open it straight away
            self.enterprise_id = enterprises[0].id
            plans = repo.list_plans(self.conn, self.enterprise_id)
            if plans and not repo.list_plans(self.conn, self.enterprise_id, self.year):
                self.year_var.set(plans[-1].year)
        self.data_changed()

        if ref_errors:
            self.after(200, self._show_reference_errors)
        if first_start_notes and "demo" in first_start_notes:
            self.after(300, self._show_welcome)

    @property
    def year(self) -> int:
        try:
            return int(self.year_var.get())
        except (tk.TclError, ValueError):
            return date.today().year

    # --- Layout -------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Åpne datamappen", command=lambda: open_path(self.paths.data_dir))
        file_menu.add_separator()
        file_menu.add_command(label="Avslutt", command=self.destroy)
        menubar.add_cascade(label="Fil", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Gi tilbakemelding…", command=lambda: give_feedback(self))
        help_menu.add_command(label="Send tilbakemelding…", command=lambda: send_feedback(self))
        help_menu.add_separator()
        help_menu.add_command(label="Om Gjødselplan", command=self._show_about)
        menubar.add_cascade(label="Hjelp", menu=help_menu)
        self.configure(menu=menubar)

    def _build_sidebar(self) -> None:
        side = ttk.Frame(self, padding=8)
        side.grid(row=0, column=0, sticky="ns")
        side.rowconfigure(3, weight=1)

        ttk.Label(side, text="Foretak", font=("TkDefaultFont", 0, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(side, text="Søk").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(side, textvariable=self.search_var, width=28).grid(row=2, column=0, sticky="ew", pady=(0, 4))
        self.enterprise_list = tk.Listbox(side, width=30, exportselection=False)
        self.enterprise_list.grid(row=3, column=0, sticky="nsew")
        self.enterprise_list.bind("<<ListboxSelect>>", self._on_sidebar_select)

        year_row = ttk.Frame(side)
        year_row.grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Label(year_row, text="År").pack(side="left")
        ttk.Spinbox(year_row, from_=2020, to=2100, textvariable=self.year_var, width=6).pack(side="left", padx=6)

        feedback = ttk.LabelFrame(side, text="Tilbakemelding", padding=6)
        feedback.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(feedback, text="Gi tilbakemelding", command=lambda: give_feedback(self)).pack(fill="x")
        ttk.Button(feedback, text="Send tilbakemelding", command=lambda: send_feedback(self)).pack(fill="x", pady=(4, 0))

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=1, sticky="nsew", padx=(0, 8), pady=8)
        self.tabs = {
            "Foretak": EnterpriseTab(self.notebook, self),
            "Eiendommer og skifter": FieldsTab(self.notebook, self),
            "Jordprøver": SoilTab(self.notebook, self),
            "Gjødslingsplan": PlanTab(self.notebook, self),
            "Journal": JournalTab(self.notebook, self),
            "Referansedata": ReferenceTab(self.notebook, self),
            "Rådata": RawDataTab(self.notebook, self),
            "Innstillinger": SettingsTab(self.notebook, self),
        }
        for title, tab in self.tabs.items():
            self.notebook.add(tab, text=title)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._refresh_visible_tab())

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 2))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(1, weight=1)
        self.status_label = ttk.Label(bar, text="")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.message_label = ttk.Label(bar, text="", foreground="#555555")
        self.message_label.grid(row=0, column=1, sticky="w", padx=16)
        self.ref_label = ttk.Label(bar, text="")
        self.ref_label.grid(row=0, column=2, sticky="e")
        self._show_reference_status()

    # --- State --------------------------------------------------------------------

    def select_enterprise(self, enterprise_id: int | None) -> None:
        self.enterprise_id = enterprise_id
        self.data_changed()

    def data_changed(self) -> None:
        """Reload everything that shows stored data."""
        if self.enterprise_id is not None and repo.get_enterprise(self.conn, self.enterprise_id) is None:
            self.enterprise_id = None
        self.reload_enterprises()
        self._stale = set(self.tabs)
        self._refresh_visible_tab()
        self._update_status()

    def reload_enterprises(self) -> None:
        enterprises = repo.list_enterprises(self.conn, self.search_var.get())
        self._enterprise_ids = [e.id for e in enterprises]
        self.enterprise_list.delete(0, "end")
        for e in enterprises:
            self.enterprise_list.insert("end", f"{e.name} ({e.place})" if e.place else e.name)
        self.enterprise_list.selection_clear(0, "end")
        if self.enterprise_id in self._enterprise_ids:
            index = self._enterprise_ids.index(self.enterprise_id)
            self.enterprise_list.selection_set(index)
            self.enterprise_list.see(index)

    def _on_sidebar_select(self, _event=None) -> None:
        selection = self.enterprise_list.curselection()
        if selection:
            enterprise_id = self._enterprise_ids[selection[0]]
            if enterprise_id != self.enterprise_id:
                self.select_enterprise(enterprise_id)

    def _refresh_visible_tab(self) -> None:
        current = self.notebook.tab(self.notebook.select(), "text")
        if current in self._stale:
            self._stale.discard(current)
            self.tabs[current].refresh()

    def set_status(self, message: str) -> None:
        self.message_label.configure(text=message)

    def settings(self) -> Settings | None:
        """Calculation settings, or None if the setting table has invalid values."""
        try:
            return repo.load_settings(self.conn)
        except ValidationError:
            return None

    def reviewer_name(self, ask: bool = True) -> str | None:
        """Name stored with reviews, changes and feedback. Asks once if not set (when ask=True)."""
        name = (repo.get_settings(self.conn).get("reviewer_name") or "").strip()
        if name or not ask:
            return name or None
        return self.change_reviewer_name()

    def change_reviewer_name(self) -> str | None:
        current = (repo.get_settings(self.conn).get("reviewer_name") or "").strip()
        name = simpledialog.askstring(
            "Navn", "Hva heter du?\nNavnet lagres sammen med vurderinger, endringer og tilbakemeldinger.",
            initialvalue=current, parent=self)
        if name and name.strip():
            repo.save_settings(self.conn, {"reviewer_name": name.strip()})
            return name.strip()
        return current or None

    def report_callback_exception(self, exc, val, tb) -> None:
        """Unexpected errors: log them and tell the user, instead of failing silently (no console on Windows)."""
        logging.getLogger(__name__).error("Uventet feil", exc_info=(exc, val, tb))
        messagebox.showerror(
            "Uventet feil",
            f"Det oppstod en uventet feil:\n{val}\n\nDetaljene er lagret i {self.paths.log_file}.\n"
            "Bruk gjerne «Gi tilbakemelding» og fortell hva du gjorde.", parent=self)

    def _show_welcome(self) -> None:
        messagebox.showinfo(
            "Velkommen",
            "Programmet er startet med demodata: foretaket «Testgård» med fire skifter, jordprøver og et "
            "utkast til gjødslingsplan. Alle data er oppdiktet.\n\n"
            "• Se planen i fanen «Gjødslingsplan».\n"
            "• Vurder normverdiene i fanen «Referansedata».\n"
            "• Bruk «Gi tilbakemelding» (til venstre) underveis, og «Send tilbakemelding» når du er ferdig.\n\n"
            f"Dataene ligger i {self.paths.data_dir}.", parent=self)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "Om Gjødselplan",
            f"Gjødselplan for frukt\nVersjon {__version__}\n\nDatamappe: {self.paths.data_dir}\n"
            f"Referansedata: {self.paths.reference_dir}\nLogg: {self.paths.log_file}", parent=self)

    def _update_status(self) -> None:
        if self.enterprise_id is None:
            self.status_label.configure(text="Velg et foretak.", foreground="")
            return
        e = repo.get_enterprise(self.conn, self.enterprise_id)
        try:
            text, problem = plan_service.status_summary(
                self.conn, self.ref, self.settings(), self.enterprise_id, self.year)
        except ValidationError as exc:
            text, problem = exc.errors[0], True
        self.status_label.configure(text=f"{e.name} · {text}", foreground=RED if problem else "")

    def reload_reference(self) -> None:
        """Reload the CSVs after editing them in Excel. On errors the previous data stays in use."""
        try:
            ref = load_reference(self.paths.reference_dir)
        except ReferenceDataError as exc:
            self.ref_errors = exc.errors
            self._show_reference_errors(keeping_previous=self.ref is not None)
            return
        self.set_reference(ref)
        self.set_status("Referansedata lastet på nytt.")

    def set_reference(self, ref: RefTables) -> None:
        self.ref = ref
        self.ref_errors = []
        self._show_reference_status()
        self.data_changed()

    def _show_reference_status(self) -> None:
        text, colour = self._reference_status()
        self.ref_label.configure(text=text, foreground=colour)

    def _reference_status(self) -> tuple[str, str]:
        if self.ref is None:
            return "Referansedata: FEIL – beregning er ikke mulig", RED
        unverified = len(self.ref.unverified_rows())
        if unverified:
            return f"Referansedata: lastet, {unverified} ikke-verifiserte rader", AMBER
        return "Referansedata: lastet", ""

    def _show_reference_errors(self, keeping_previous: bool = False) -> None:
        shown = self.ref_errors[:_MAX_ERRORS_SHOWN]
        text = "\n".join(f"• {e}" for e in shown)
        hidden = len(self.ref_errors) - len(shown)
        if hidden:
            text += f"\n… og {hidden} feil til."
        messagebox.showerror(
            "Feil i referansedata",
            f"Referansefilene i {self.paths.reference_dir} har feil og er ikke lastet"
            + (" (forrige versjon er fortsatt i bruk)" if keeping_previous else "") + f":\n\n{text}",
            parent=self,
        )
