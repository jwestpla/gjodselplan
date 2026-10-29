"""Journal: actual fertilizer applications per field (§ 27). Always editable, also for finished plans."""

from __future__ import annotations

import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import plan_service as ps
from .. import repo
from ..models import Application, Plan
from ..plan_view import PlanView
from ..units import format_date, format_number, parse_date
from ..validation import ValidationError
from .plan_tab import ApplicationDialog
from .widgets import EditDialog, button_row, scrolled_tree, selected_id, show_errors

if TYPE_CHECKING:
    from .main_window import MainWindow


class JournalTab(ttk.Frame):
    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=8)
        self.app = app
        self.plan: Plan | None = None
        self.view: PlanView | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=2)
        self.rowconfigure(3, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.title_label = ttk.Label(top, text="", font=("TkDefaultFont", 0, "bold"))
        self.title_label.pack(side="left")
        button_row(top, [
            ("Opprett journal", self.create),
            ("Ny registrering", self.add),
            ("Rediger", self.edit),
            ("Slett", self.delete),
            ("Kopier planlagt → utført…", self.copy_planned),
            ("Eksporter journal (PDF)", self.export_pdf),
        ]).pack(side="left", padx=(12, 0))

        frame, self.tree = scrolled_tree(self, [
            ("date", "Dato", 85), ("field", "Skifte", 150), ("product", "Gjødseltype", 190), ("kg", "kg/daa", 60),
            ("total", "Totalt kg", 70), ("n", "N kg/daa", 65), ("p", "P kg/daa", 65), ("k", "K kg/daa", 65),
            ("by", "Utført av", 90), ("note", "Notat", 140),
        ], height=12, numeric=("kg", "total", "n", "p", "k"))
        frame.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _e: self.edit())

        ttk.Label(self, text="Sum per skifte og for foretaket", font=("TkDefaultFont", 0, "bold")).grid(
            row=2, column=0, sticky="w", pady=(8, 2))
        frame, self.sum_tree = scrolled_tree(self, [
            ("field", "Skifte", 190), ("area", "Daa", 60), ("n", "N kg/daa", 70), ("p", "P kg/daa", 70),
            ("k", "K kg/daa", 70), ("n_kg", "N kg", 70), ("p_kg", "P kg", 70), ("k_kg", "K kg", 70),
        ], height=6, numeric=("area", "n", "p", "k", "n_kg", "p_kg", "k_kg"))
        frame.grid(row=3, column=0, sticky="nsew")
        self.total_label = ttk.Label(self, text="")
        self.total_label.grid(row=4, column=0, sticky="w", pady=(4, 0))

    def refresh(self) -> None:
        self.plan = self.view = None
        self.tree.delete(*self.tree.get_children())
        self.sum_tree.delete(*self.sum_tree.get_children())
        self.total_label.configure(text="")
        eid, year = self.app.enterprise_id, self.app.year
        if eid is None:
            self.title_label.configure(text="Velg et foretak.")
            return
        self.plan = repo.latest_plan(self.app.conn, eid, year)
        if self.plan is None:
            self.title_label.configure(
                text=f"Ingen journal for {year}. Trykk «Opprett journal» (journal er påkrevd også uten plan).")
            return
        settings = self.app.settings()
        if self.app.ref is None or settings is None:
            self.title_label.configure(text="Referansedata eller innstillinger mangler – kan ikke vise journalen.")
            return
        self.view = ps.build_view(self.app.conn, self.app.ref, settings, self.plan)
        self.title_label.configure(text=f"Gjødseljournal {year} (plan versjon {self.plan.version}, {self.plan.status})")
        entries = [(line, row) for row in self.view.rows for line in row.actual]
        for line, row in sorted(entries, key=lambda e: (e[0].date or "", e[1].field_no)):
            self.tree.insert("", "end", iid=str(line.id), values=(
                format_date(line.date), row.label, line.product_name, format_number(line.kg_per_daa),
                format_number(line.total_kg, 0), format_number(line.supply["n"], 2, "?"),
                format_number(line.supply["p"], 2, "?"), format_number(line.supply["k"], 2, "?"),
                line.performed_by or "", line.note or "",
            ))
        totals = self.view.journal_totals()
        for f in totals["fields"]:
            r = f["row"]
            self.sum_tree.insert("", "end", values=(
                r.label, format_number(r.area_daa), *(format_number(f[f"{n}_daa"], 2) for n in "npk"),
                *(format_number(f[f"{n}_kg"], 1) for n in "npk"),
            ))
        self.total_label.configure(
            text=f"Foretaket totalt: N {format_number(totals['n_kg'], 1)} kg · P {format_number(totals['p_kg'], 1)} kg"
                 f" · K {format_number(totals['k_kg'], 1)} kg")

    def _season_choices(self) -> list[tuple[int, str, float]]:
        return [(r.season_id, r.label, r.area_daa) for r in self.view.rows] if self.view else []

    def create(self) -> None:
        if self.app.enterprise_id is None or self.plan is not None:
            return
        settings = self.app.settings()
        if self.app.ref is None or settings is None:
            return
        try:
            ps.create_plan(self.app.conn, self.app.ref, settings, self.app.enterprise_id, self.app.year)
        except ValidationError as exc:
            show_errors(self, exc.errors, "Kan ikke opprette")
            return
        self.app.data_changed()

    def add(self) -> None:
        if self.view is None:
            messagebox.showinfo("Journal", "Opprett journalen for året først.", parent=self)
            return
        app = Application(kind="utfort", date=date.today().isoformat())
        if ApplicationDialog(self, self.app, app, 0.0, self._season_choices()).show():
            self.app.data_changed()

    def edit(self) -> None:
        app_id = selected_id(self.tree)
        if app_id is None or self.view is None:
            return
        app = repo.get_application(self.app.conn, app_id)
        if ApplicationDialog(self, self.app, app, 0.0, self._season_choices()).show():
            self.app.data_changed()

    def delete(self) -> None:
        app_id = selected_id(self.tree)
        if app_id is None:
            return
        if messagebox.askyesno("Slett", "Slette registreringen fra journalen?", icon="warning", parent=self):
            repo.delete_application(self.app.conn, app_id)
            self.app.data_changed()

    def copy_planned(self) -> None:
        if self.view is None:
            return
        if not any(r.planned for r in self.view.rows):
            messagebox.showinfo("Kopier", "Planen har ingen planlagt gjødsling å kopiere.", parent=self)
            return
        if CopyPlannedDialog(self, self.app, self.view).show():
            self.app.data_changed()

    def export_pdf(self) -> None:
        if self.view is None:
            return
        from .pdf_dialogs import export_journal_pdf
        export_journal_pdf(self, self.app, self.view)


class CopyPlannedDialog(EditDialog):
    """Pick planned applications and a date; creates the matching journal entries."""

    def __init__(self, master, app: MainWindow, view: PlanView):
        super().__init__(master, "Kopier planlagt → utført")
        self.app = app
        f = self.form
        f.entry("date", "Dato * (dd.mm.åååå)", width=12)
        f.entry("performed_by", "Utført av")
        f.set("date", format_date(date.today().isoformat()))
        ttk.Label(self.extra, text="Planlagt gjødsling som er utført (velg én eller flere):").pack(anchor="w")
        self.lines = [(row, line) for row in view.rows for line in row.planned]
        self.listbox = tk.Listbox(self.extra, selectmode="multiple", height=min(12, max(4, len(self.lines))),
                                  width=70, exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        for row, line in self.lines:
            self.listbox.insert("end", f"{row.label} – {line.timing_label}: {line.product_name} "
                                       f"{format_number(line.kg_per_daa)} kg/daa")
        ttk.Label(self.extra, text="Mengdene kan justeres etterpå under Rediger.", foreground="#666666").pack(anchor="w")

    def save(self) -> None:
        errors = []
        try:
            done = parse_date(self.form.get("date"))
        except ValueError:
            done = None
        if done is None:
            errors.append("Dato må fylles ut (dd.mm.åååå).")
        chosen = [self.lines[i][1].id for i in self.listbox.curselection()]
        if not chosen:
            errors.append("Velg minst én planlagt gjødsling.")
        if errors:
            raise ValidationError(errors)
        n = ps.copy_planned_to_actual(self.app.conn, self.app.ref, chosen, done.isoformat(),
                                      self.form.get("performed_by") or None)
        self.app.set_status(f"{n} registreringer lagt i journalen.")
