"""Gjødslingsplan: the year plan for the selected enterprise (PLAN §7)."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from .. import plan_service as ps
from .. import repo
from ..models import Application, Plan
from ..plan_view import AppLine, FieldRow, PlanView
from ..soil import sample_validity
from ..units import format_date, format_number, format_signed, parse_date, parse_number
from ..validation import ValidationError
from .widgets import AMBER, GREEN, GREY, RED, EditDialog, Form, button_row, scrolled_tree, show_errors

if TYPE_CHECKING:
    from .main_window import MainWindow

_BALANCE_COLOURS = {"ok": GREEN, "under": AMBER, "over": RED}
_BALANCE_TEXT = {"ok": "ok", "under": "under behov", "over": "over behov"}
_SEASON_KEYS = ("expected_yield_kg_daa", "growth_class", "forgrode", "soil_sample_id",
                "override_n", "override_p", "override_k", "override_reason", "notes")


def serious(warnings: list[str]) -> list[str]:
    """Row warnings that need attention (the unverified-norms note is on every row and shown separately)."""
    return [w for w in warnings if not w.startswith("Bygger på ikke-verifiserte")]


class PlanTab(ttk.Frame):
    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=8)
        self.app = app
        self.plan: Plan | None = None
        self.view: PlanView | None = None
        self.selected_plan_id: int | None = None
        self.highlight: set[int] = set()  # field ids whose stage changed after "Kopier fra fjorår"
        self._previous_selection: tuple = ()
        self._context_key: tuple = ()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        self.title_label = ttk.Label(top, text="", font=("TkDefaultFont", 0, "bold"))
        self.title_label.pack(side="left")
        self.version_var = tk.StringVar()
        self.version_combo = ttk.Combobox(top, textvariable=self.version_var, state="readonly", width=16)
        self.version_combo.pack(side="left", padx=8)
        self.version_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_version_selected())
        self.buttons = button_row(top, [
            ("Opprett plan", self.create_plan),
            ("Beregn alle", self.recalculate),
            ("Kopier fra fjorår", self.copy_last_year),
            ("Bruk mal…", self.use_template),
            ("Legg til nye skifter", self.add_fields),
            ("Ferdigstill", self.finalize),
            ("Ny versjon…", self.new_version),
            ("Eksporter PDF", self.export_pdf),
        ])
        self.buttons.pack(side="left", padx=(8, 0))

        self.warning_text = tk.Text(self, height=4, wrap="word", font="TkDefaultFont", relief="flat")
        self.warning_text.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        self.warning_text.tag_configure("red", foreground=RED)
        self.warning_text.tag_configure("amber", foreground=AMBER)
        self.warning_text.tag_configure("green", foreground=GREEN)
        self.warning_text.tag_configure("bold", font=("TkDefaultFont", 0, "bold"))

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.grid(row=2, column=0, sticky="nsew")

        tree_frame, self.tree = scrolled_tree(panes, [
            ("no", "Nr.", 45), ("name", "Navn", 130), ("culture", "Kultur", 110), ("area", "Daa", 55),
            ("stage", "Stadium", 75), ("req_n", "Behov N", 65), ("req_p", "P", 50), ("req_k", "K", 50),
            ("pl_n", "Planlagt N", 75), ("pl_p", "P", 50), ("pl_k", "K", 50), ("p_status", "P-status", 70),
            ("warn", "Varsler", 65),
        ], height=7, numeric=("area", "req_n", "req_p", "req_k", "pl_n", "pl_p", "pl_k"))
        panes.add(tree_frame, weight=1)
        for tag, colour in (("p_over", RED), ("soil", AMBER), ("unfertilized", GREY)):
            self.tree.tag_configure(tag, foreground=colour)
        self.tree.tag_configure("stage_changed", background="#fff3c4")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())

        detail = ttk.Frame(panes)
        panes.add(detail, weight=2)
        detail.columnconfigure(1, weight=1)
        detail.rowconfigure(0, weight=1)

        season_box = ttk.LabelFrame(detail, text="Sesong", padding=6)
        season_box.grid(row=0, column=0, sticky="nsw")
        f = self.season_form = Form(season_box)
        f.pack(anchor="nw")
        f.check("fertilized", "Gjødsles i år")
        f.entry("expected_yield_kg_daa", "Forventet avling (kg/daa)", width=10)
        f.combo("growth_class", "Nyvekst", [], width=22)
        f.entry("forgrode", "Forgrøde", width=24)
        f.combo("soil_sample_id", "Jordprøve", [], width=30)
        f.entry("override_n", "Overstyr N (kg/daa)", width=8)
        f.entry("override_p", "Overstyr P (kg/daa)", width=8)
        f.entry("override_k", "Overstyr K (kg/daa)", width=8)
        f.entry("override_reason", "Begrunnelse for overstyring", width=30)
        f.text("notes", "Notater", height=2, width=30)
        self.save_season_button = ttk.Button(season_box, text="Lagre sesong", command=self.save_season)
        self.save_season_button.pack(anchor="w", pady=(6, 0))

        calc_box = ttk.LabelFrame(detail, text="Behov og begrunnelse", padding=6)
        calc_box.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        calc_box.columnconfigure(0, weight=1)
        calc_box.rowconfigure(0, weight=1)
        self.calc_text = tk.Text(calc_box, wrap="word", height=12, font="TkDefaultFont")
        self.calc_text.grid(row=0, column=0, sticky="nsew")
        self.calc_text.tag_configure("bold", font=("TkDefaultFont", 0, "bold"))
        self.calc_text.tag_configure("red", foreground=RED)
        self.calc_text.tag_configure("amber", foreground=AMBER)

        app_box = ttk.LabelFrame(detail, text="Planlagt gjødsling", padding=6)
        app_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        app_box.columnconfigure(0, weight=1)
        frame, self.app_tree = scrolled_tree(app_box, [
            ("timing", "Tidspunkt", 150), ("product", "Produkt", 200), ("kg", "kg/daa", 60), ("n", "N", 50),
            ("p", "P", 50), ("k", "K", 50), ("total", "Totalt kg", 75), ("note", "Notat", 160),
        ], height=4, numeric=("kg", "n", "p", "k", "total"))
        frame.grid(row=0, column=0, sticky="ew")
        self.app_tree.bind("<Double-1>", lambda _e: self.edit_application())
        bottom = ttk.Frame(app_box)
        bottom.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.app_buttons = button_row(bottom, [
            ("Legg til", self.add_application),
            ("Rediger", self.edit_application),
            ("Slett", self.delete_application),
        ])
        self.app_buttons.pack(side="left")
        self.balance_labels = {n: tk.Label(bottom, text="") for n in ("n", "p", "k")}
        for label in self.balance_labels.values():
            label.pack(side="left", padx=(16, 0))

    # --- Loading -------------------------------------------------------------------

    def _context(self):
        """(ref, settings) or None with a message in the warning box."""
        if self.app.ref is None:
            self._set_warnings([("Referansedata er ikke lastet – rett feilene i referansefilene og start på nytt.",
                                 "red")])
            return None
        settings = self.app.settings()
        if settings is None:
            self._set_warnings([("Innstillingene er ugyldige – se Innstillinger.", "red")])
            return None
        return self.app.ref, settings

    def refresh(self) -> None:
        self.plan = self.view = None
        context_key = (self.app.enterprise_id, self.app.year)
        if context_key != self._context_key:
            self.highlight.clear()
            self._context_key = context_key
        self._previous_selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        eid, year = self.app.enterprise_id, self.app.year
        if eid is None:
            self.title_label.configure(text="Velg et foretak.")
            self.version_combo.configure(values=[])
            self.version_var.set("")
            self._set_warnings([])
            self._show_detail()
            return
        plans = repo.list_plans(self.app.conn, eid, year)
        self.version_combo.configure(values=[self._version_label(p) for p in plans])
        if self.selected_plan_id not in {p.id for p in plans}:
            self.selected_plan_id = plans[-1].id if plans else None
        self.plan = next((p for p in plans if p.id == self.selected_plan_id), None)
        if self.plan is None:
            self.title_label.configure(text=f"Ingen plan for {year}")
            self.version_var.set("")
            self._set_warnings([(f"Trykk «Opprett plan» eller «Kopier fra fjorår» for å lage planen for {year}.", "")])
            self._show_detail()
            return
        self.version_var.set(self._version_label(self.plan))
        newest = self.plan.id == plans[-1].id
        state = "ferdigstilt" if self.plan.finished else "utkast"
        self.title_label.configure(
            text=f"Plan {year} · versjon {self.plan.version} · {state}" + ("" if newest else " (eldre versjon)"))

        context = self._context()
        if context is None:
            return
        ref, settings = context
        try:
            self.view = ps.build_view(self.app.conn, ref, settings, self.plan)
        except ValidationError as exc:
            self._set_warnings([(e, "red") for e in exc.errors])
            return
        self._fill_tree()
        self._fill_warnings()
        self._show_detail()

    @staticmethod
    def _version_label(p: Plan) -> str:
        return f"Versjon {p.version} ({p.status})"

    def _on_version_selected(self) -> None:
        plans = repo.list_plans(self.app.conn, self.app.enterprise_id, self.app.year)
        label = self.version_var.get()
        self.selected_plan_id = next((p.id for p in plans if self._version_label(p) == label), None)
        self.refresh()

    def _fill_tree(self) -> None:
        selection = self._previous_selection
        for r in self.view.rows:
            tags = []
            if r.p_over_field_limit:
                tags.append("p_over")
            elif r.fertilized and not r.soil_valid:
                tags.append("soil")
            elif not r.fertilized:
                tags.append("unfertilized")
            if r.field_id in self.highlight:
                tags.append("stage_changed")
            req = [format_number(r.effective[n], empty="–") + ("*" if r.override[n] is not None else "")
                   for n in ("n", "p", "k")]
            planned = [format_number(r.planned_supply[n], empty="?") for n in ("n", "p", "k")]
            p_status = "–" if r.p_over_field_limit is None else ("OVER" if r.p_over_field_limit else "OK")
            n_warn = len(serious(r.warnings))
            self.tree.insert("", "end", iid=str(r.season_id), tags=tags, values=(
                r.field_no, r.name, r.culture_name, format_number(r.area_daa), r.stage or "–", *req, *planned,
                p_status if r.fertilized else "ikke gjødslet", f"⚠ {n_warn}" if n_warn else "",
            ))
        if selection and self.tree.exists(selection[0]):
            self.tree.selection_set(selection[0])
        elif self.view.rows:
            self.tree.selection_set(str(self.view.rows[0].season_id))

    def _fill_warnings(self) -> None:
        v = self.view
        lines = [(f"{v.legal_text} · {format_number(v.fertilized_area)} daa gjødslet", "bold"),
                 (v.p_summary, "green" if v.p_ok else ("red" if v.p_ok is False else "amber"))]
        if v.change_reason:
            lines.append((f"Endringsbegrunnelse (versjon {v.version}): {v.change_reason}", ""))
        for w in v.warnings:
            lines.append((f"⚠ {w}", "amber" if w.startswith(ps.UNVERIFIED_WARNING) else "red"))
        self._set_warnings(lines)

    def _set_warnings(self, lines: list[tuple[str, str]]) -> None:
        t = self.warning_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for text, tag in lines:
            t.insert("end", text + "\n", tag)
        t.configure(state="disabled", height=max(2, min(len(lines), 7)))

    def _selected_row(self) -> FieldRow | None:
        selection = self.tree.selection()
        if not selection or self.view is None:
            return None
        return self.view.row(int(selection[0]))

    # --- Detail panel -----------------------------------------------------------------

    def _show_detail(self) -> None:
        row = self._selected_row()
        editable = row is not None and self.plan is not None and not self.plan.finished
        self._fill_season_form(row)
        self.season_form.set_enabled(editable)
        self.save_season_button.state(["!disabled"] if editable else ["disabled"])
        for child in self.app_buttons.winfo_children():
            child.state(["!disabled"] if editable else ["disabled"])
        self._fill_calc_text(row)
        self._fill_applications(row)

    def _fill_season_form(self, row: FieldRow | None) -> None:
        f = self.season_form
        ref = self.app.ref
        growth = [("", "ikke satt")] + ([(g.growth_class, g.label_nb) for g in ref.n_growth_adjustment.values()]
                                        if ref else [])
        f.set_choices("growth_class", growth)
        samples = [("", "ingen")]
        if row is not None and self.plan is not None:
            settings = self.app.settings()
            for s in repo.samples_for_field(self.app.conn, row.field_id):
                state = ""
                if settings is not None:
                    v = sample_validity(s.method, s.year, self.plan.year, settings.soil_max_age_years)
                    state = " (gyldig)" if v.valid else f" ({v.reason})"
                samples.append((str(s.id), f"{format_date(s.sample_date)} {s.label or ''}{state}".replace("  ", " ")))
        f.set_choices("soil_sample_id", samples)
        if row is None:
            for key in _SEASON_KEYS:
                f.set(key, None)
            f.set("fertilized", False)
            return
        f.set("fertilized", row.fertilized)
        f.set("expected_yield_kg_daa", row.expected_yield_kg_daa)
        f.set("growth_class", row.growth_class or "")
        f.set("forgrode", row.forgrode)
        f.set("soil_sample_id", str(row.soil_sample_id) if row.soil_sample_id else "")
        for n in ("n", "p", "k"):
            f.set(f"override_{n}", row.override[n])
        f.set("override_reason", row.override_reason)
        f.set("notes", row.notes)

    def _fill_calc_text(self, row: FieldRow | None) -> None:
        t = self.calc_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if row is not None:
            t.insert("end", f"{row.label} · {row.culture_name} · {format_number(row.area_daa)} daa · "
                            f"stadium {row.stage or '–'}\n", "bold")
            t.insert("end", f"Jordprøve: {row.soil_sample_text}\n\n")
            t.insert("end", "Behov (kg/daa)        N      P      K\n", "bold")
            t.insert("end", _npk_line("Beregnet", row.req))
            if any(v is not None for v in row.override.values()):
                t.insert("end", _npk_line("Overstyrt", row.override))
                t.insert("end", f"   Begrunnelse: {row.override_reason}\n")
            t.insert("end", _npk_line("Gjeldende", row.effective), "bold")
            t.insert("end", _npk_line("Planlagt", row.planned_supply))
            t.insert("end", "\nBeregning\n", "bold")
            for s in row.steps:
                delta = "" if s.kind in ("start", "note") else f" ({format_signed(s.delta)})"
                t.insert("end", f"  {s.nutrient}: {s.label_nb}{delta} → {format_number(s.value_after)}\n")
            if row.rationale:
                t.insert("end", "\nTekst i planen\n", "bold")
                t.insert("end", row.rationale + "\n")
            if row.warnings:
                t.insert("end", "\nVarsler\n", "bold")
                for w in row.warnings:
                    t.insert("end", f"⚠ {w}\n", "red" if w in serious(row.warnings) else "amber")
            if row.soil_notes:
                t.insert("end", "\nJordstatus (påvirker ikke tallene)\n", "bold")
                for note in row.soil_notes:
                    t.insert("end", f"• {note}\n")
        t.configure(state="disabled")

    def _fill_applications(self, row: FieldRow | None) -> None:
        self.app_tree.delete(*self.app_tree.get_children())
        for label in self.balance_labels.values():
            label.configure(text="")
        if row is None:
            return
        for line in row.planned:
            self.app_tree.insert("", "end", iid=str(line.id), values=_app_values(line))
        for n, label in self.balance_labels.items():
            status = row.balance_status[n]
            if status is None:
                label.configure(text=f"{n.upper()}: balanse ukjent", fg=GREY)
                continue
            label.configure(
                text=f"{n.upper()}: {format_number(row.planned_supply[n])} / {format_number(row.effective[n])} "
                     f"({format_signed(row.balance[n])}, {_BALANCE_TEXT[status]})",
                fg=_BALANCE_COLOURS[status],
            )

    # --- Actions ------------------------------------------------------------------------

    def _run(self, action, success: str | None = None) -> bool:
        context = self._context()
        if context is None:
            return False
        try:
            action(*context)
        except ValidationError as exc:
            show_errors(self, exc.errors, "Kan ikke utføre")
            return False
        self.app.data_changed()
        if success:
            self.app.set_status(success)
        return True

    def create_plan(self) -> None:
        if self.app.enterprise_id is None:
            return
        year = self.app.year
        self.selected_plan_id = None
        self._run(lambda ref, st: ps.create_plan(self.app.conn, ref, st, self.app.enterprise_id, year),
                  f"Plan for {year} opprettet.")

    def recalculate(self) -> None:
        if self.plan is None:
            return
        if self.plan.finished:
            messagebox.showinfo("Beregn alle", "Planen er ferdigstilt og beregnes ikke på nytt. "
                                               "Lag en ny versjon for å endre den.", parent=self)
            return
        self._run(lambda ref, st: ps.recalculate(self.app.conn, ref, st, self.plan), "Behov beregnet på nytt.")

    def copy_last_year(self) -> None:
        if self.app.enterprise_id is None:
            return
        result = {}

        def action(ref, st):
            result["plan"], result["changed"] = ps.copy_from_last_year(
                self.app.conn, ref, st, self.app.enterprise_id, self.app.year)

        self.selected_plan_id = None
        if not self._run(action, f"Plan for {self.app.year} kopiert fra {self.app.year - 1}."):
            return
        changed = result["changed"]
        fields = {f"{f.field_no} {f.name}": f.id for f in repo.list_fields(self.app.conn, self.app.enterprise_id)}
        self.highlight = {fields[c.split(":")[0]] for c in changed if c.split(":")[0] in fields}
        self.refresh()
        text = "Planen er kopiert og behovet er beregnet på nytt med årets jordprøver og alder."
        if changed:
            text += "\n\nSkifter som har byttet stadium (markert i gult):\n" + "\n".join(f"• {c}" for c in changed)
        messagebox.showinfo("Kopier fra fjorår", text, parent=self)

    def add_fields(self) -> None:
        if self.plan is None:
            return
        result = {}

        def action(ref, st):
            result["n"] = ps.add_missing_fields(self.app.conn, ref, st, self.plan)

        if self._run(action):
            self.app.set_status(f"{result['n']} nye skifter lagt til i planen.")

    def use_template(self) -> None:
        row = self._selected_row()
        if self.plan is None or row is None or self.app.ref is None:
            messagebox.showinfo("Bruk mal", "Velg et skifte i planen først.", parent=self)
            return
        dialog = TemplateDialog(self, self.app, self.view, row)
        if dialog.show():
            self.app.data_changed()

    def finalize(self) -> None:
        if self.plan is None or self.view is None:
            return
        problems = list(self.view.warnings)
        for r in self.view.rows:
            problems.extend(f"Skifte {r.label}: {w}" for w in serious(r.warnings))
        text = f"Ferdigstille plan {self.plan.year} versjon {self.plan.version}?\n\n" \
               "Planen låses og et øyeblikksbilde lagres. Endringer krever en ny versjon."
        if problems:
            shown = problems[:15]
            text += "\n\nPlanen har disse varslene – bekreft at du har vurdert dem:\n" + "\n".join(f"• {p}" for p in shown)
            if len(problems) > len(shown):
                text += f"\n… og {len(problems) - len(shown)} til."
        if messagebox.askyesno("Ferdigstill", text, icon="warning" if problems else "question", parent=self):
            self._run(lambda ref, st: ps.finalize(self.app.conn, ref, st, self.plan), "Planen er ferdigstilt.")

    def new_version(self) -> None:
        if self.plan is None:
            return
        reason = simpledialog.askstring(
            "Ny versjon", "Begrunnelse for endringen (påkrevd, kommer med i planen):", parent=self)
        if reason is None:
            return
        result = {}

        def action(ref, st):
            result["plan"] = ps.new_version(self.app.conn, ref, st, self.plan, reason)

        if self._run(action):
            self.selected_plan_id = result["plan"].id
            self.app.data_changed()
            self.app.set_status(f"Versjon {result['plan'].version} opprettet.")

    def export_pdf(self) -> None:
        if self.view is None:
            return
        from .pdf_dialogs import export_plan_pdf  # reportlab is only needed here
        export_plan_pdf(self, self.app, self.view)

    # --- Season and applications ------------------------------------------------------------

    def save_season(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        f = self.season_form
        errors: list[str] = []
        season = repo.get_field_season(self.app.conn, row.season_id)
        sample = f.get("soil_sample_id")
        season = replace(
            season,
            fertilized=bool(f.get("fertilized")),
            expected_yield_kg_daa=f.get_number("expected_yield_kg_daa", errors),
            growth_class=f.get("growth_class") or None,
            forgrode=f.get("forgrode"),
            soil_sample_id=int(sample) if sample else None,
            override_n=f.get_number("override_n", errors),
            override_p=f.get_number("override_p", errors),
            override_k=f.get_number("override_k", errors),
            override_reason=f.get("override_reason"),
            notes=f.get("notes"),
        )
        if errors:
            show_errors(self, errors)
            return
        self._run(lambda ref, st: ps.save_season(self.app.conn, ref, st, season), f"Lagret skifte {row.label}.")

    def _selected_line(self) -> AppLine | None:
        row = self._selected_row()
        selection = self.app_tree.selection()
        if row is None or not selection:
            return None
        return next((line for line in row.planned if str(line.id) == selection[0]), None)

    def add_application(self) -> None:
        row = self._selected_row()
        if row is not None and self.app.ref is not None:
            app = Application(field_season_id=row.season_id, kind="planlagt")
            if ApplicationDialog(self, self.app, app, row.area_daa).show():
                self.app.data_changed()

    def edit_application(self) -> None:
        line = self._selected_line()
        if line is None or self.plan is None or self.plan.finished:
            return
        app = repo.get_application(self.app.conn, line.id)
        if ApplicationDialog(self, self.app, app, self._selected_row().area_daa).show():
            self.app.data_changed()

    def delete_application(self) -> None:
        line = self._selected_line()
        if line is None:
            return
        if messagebox.askyesno("Slett", f"Slette {line.product_name} {format_number(line.kg_per_daa)} kg/daa?",
                               parent=self):
            try:
                repo.delete_application(self.app.conn, line.id)
            except ValidationError as exc:
                show_errors(self, exc.errors, "Kan ikke slette")
                return
            self.app.data_changed()


def _npk_line(label: str, values: dict) -> str:
    cells = "".join(f"{format_number(values.get(n), empty='–'):>7}" for n in ("n", "p", "k"))
    return f"  {label:<18}{cells}\n"


def _app_values(line: AppLine) -> tuple:
    return (line.timing_label or (format_date(line.date) if line.date else ""), line.product_name,
            format_number(line.kg_per_daa), format_number(line.supply["n"], 2, "?"),
            format_number(line.supply["p"], 2, "?"), format_number(line.supply["k"], 2, "?"),
            format_number(line.total_kg, 0), line.note or "")


class ApplicationDialog(EditDialog):
    """Planned application (product, kg/daa, timing) or, for the journal, an actual one (with date)."""

    def __init__(self, master, app: MainWindow, application: Application, area_daa: float,
                 season_choices: list[tuple[int, str, float]] | None = None):
        """season_choices: (field_season_id, label, area) to let the user pick the field (journal)."""
        actual = application.kind == "utfort"
        title = ("Ny" if application.id is None else "Rediger") + (" utført gjødsling" if actual else " planlagt gjødsling")
        super().__init__(master, title)
        self.app = app
        self.application = application
        self.area_daa = area_daa
        self.areas = {sid: area for sid, _, area in season_choices or []}
        ref = app.ref
        f = self.form
        if season_choices:
            f.combo("field_season_id", "Skifte *", [(str(sid), label) for sid, label, _ in season_choices], width=40)
            f.set("field_season_id", str(application.field_season_id) if application.field_season_id else "")
            f.var("field_season_id").trace_add("write", lambda *_: self._update_summary())
        if actual:
            f.entry("date", "Dato * (dd.mm.åååå)", width=12)
        f.combo("product_code", "Produkt *", [(p.code, _product_label(p)) for p in ref.products.values()], width=40)
        f.entry("kg_per_daa", "Mengde (kg/daa) *", width=10)
        f.combo("timing_code", "Tidspunkt", [("", "–")] + [(t.code, t.label_nb) for t in ref.timings.values()],
                width=24)
        if actual:
            f.entry("performed_by", "Utført av")
            f.entry("method", "Metode (f.eks. breispredd, gjødselvanning)")
        f.entry("note", "Notat", width=40)
        f.label("summary", "Gir (kg/daa)")
        for key in ("product_code", "kg_per_daa", "timing_code", "note") + (("performed_by", "method") if actual else ()):
            f.set(key, getattr(application, key) or "")
        if actual:
            f.set("date", format_date(application.date))
        for key in ("product_code", "kg_per_daa"):
            f.var(key).trace_add("write", lambda *_: self._update_summary())
        self._update_summary()

    def _update_summary(self) -> None:
        product = self.app.ref.products.get(self.form.get("product_code"))
        try:
            kg = parse_number(self.form.get("kg_per_daa"))
        except ValueError:
            kg = None
        if product is None or kg is None:
            self.form.set("summary", "–")
            return
        parts = [f"{n.upper()} {format_number(kg * getattr(product, n) / 100, 2)}" for n in ("n", "p", "k")]
        area = self._area()
        total = f"   ·   totalt {format_number(kg * area, 0)} kg" if area else ""
        self.form.set("summary", "  ".join(parts) + total)

    def _area(self) -> float | None:
        if self.areas:
            chosen = self.form.get("field_season_id")
            return self.areas.get(int(chosen)) if chosen else None
        return self.area_daa

    def save(self) -> None:
        f = self.form
        errors: list[str] = []
        values = dict(product_code=f.get("product_code"), kg_per_daa=f.get_number("kg_per_daa", errors),
                      timing_code=f.get("timing_code") or None, note=f.get("note"))
        if self.application.kind == "utfort":
            try:
                done = parse_date(f.get("date"))
            except ValueError:
                done = None
                errors.append(f"«Dato»: «{f.get('date')}» er ikke en gyldig dato.")
            values.update(date=done.isoformat() if done else None, performed_by=f.get("performed_by"),
                          method=f.get("method"))
        if self.areas:
            chosen = f.get("field_season_id")
            if not chosen:
                errors.append("Skifte må velges.")
            else:
                values["field_season_id"] = int(chosen)
        if errors:
            raise ValidationError(errors)
        ps.save_application(self.app.conn, self.app.ref, replace(self.application, **values))


def _product_label(p) -> str:
    return f"{p.name} (N {format_number(p.n)} · P {format_number(p.p)} · K {format_number(p.k)})"


class TemplateDialog(EditDialog):
    def __init__(self, master, app: MainWindow, view: PlanView, row: FieldRow):
        super().__init__(master, "Bruk mal")
        self.app, self.view, self.row = app, view, row
        ref = app.ref
        names = ps.template_names(ref)
        f = self.form
        f.combo("template", "Mal (templates.csv)", [(n, f"{n} ({ps.template_stage(ref, n)})") for n in names], width=36)
        f.combo("scope", "Bruk på", [("one", f"valgt skifte ({row.label})"),
                                     ("stage", "alle gjødslede skifter i malens stadium")], width=36)
        f.label("preview", "Innhold")
        matching = next((n for n in names if ps.template_stage(ref, n) == row.stage), names[0] if names else "")
        f.set("template", matching)
        f.set("scope", "one")
        f.var("template").trace_add("write", lambda *_: self._preview())
        self._preview()

    def _preview(self) -> None:
        ref = self.app.ref
        apps = ps.template_applications(ref, self.form.get("template"))
        self.form.set("preview", "\n".join(
            f"{ref.timings[a.timing_code].label_nb}: {ref.products[a.product_code].name} "
            f"{format_number(a.kg_per_daa)} kg/daa" for a in apps))

    def save(self) -> bool | None:
        ref = self.app.ref
        name = self.form.get("template")
        if self.form.get("scope") == "stage":
            stage = ps.template_stage(ref, name)
            targets = [r for r in self.view.rows if r.fertilized and r.stage == stage]
        else:
            targets = [self.row]
        if not targets:
            raise ValidationError("Ingen skifter passer til valget.")
        with_existing = [r.label for r in targets if r.planned]
        if with_existing and not messagebox.askyesno(
            "Erstatte?", "Planlagt gjødsling erstattes for:\n" + "\n".join(f"• {t}" for t in with_existing),
            parent=self,
        ):
            return False
        if self.row.stage != ps.template_stage(ref, name) and self.form.get("scope") == "one":
            if not messagebox.askyesno(
                "Annet stadium", f"Malen er for stadium «{ps.template_stage(ref, name)}», men skiftet er "
                                 f"«{self.row.stage}». Bruke likevel?", parent=self,
            ):
                return False
        ps.apply_template(self.app.conn, ref, [r.season_id for r in targets], name)
        return None
