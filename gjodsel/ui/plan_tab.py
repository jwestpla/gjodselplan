"""Gjødslingsplan: the year plan for the selected enterprise (PLAN §7)."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from .. import plan_service as ps
from ..calc import NO_VALID_SOIL
from .. import repo
from ..models import Application, Plan
from ..plan_view import AppLine, FieldRow, PlanView
from ..soil import sample_validity
from ..units import format_date, format_number, format_signed, parse_date, parse_number
from ..validation import ValidationError
from .widgets import AMBER, GREEN, GREY, RED, EditDialog, Form, scrolled_tree, show_errors, stripe

if TYPE_CHECKING:
    from .main_window import MainWindow

_BALANCE_COLOURS = {"ok": GREEN, "under": AMBER, "over": RED}
_BALANCE_TEXT = {"ok": "ok", "under": "under", "over": "over"}
_NPK = ("n", "p", "k")
_BOLD = ("TkDefaultFont", 0, "bold")
_SEASON_KEYS = ("expected_yield_kg_daa", "growth_class", "forgrode", "soil_sample_id",
                "override_n", "override_p", "override_k", "override_reason", "notes")


def serious(warnings: list[str]) -> list[str]:
    """Row warnings that need attention (the unverified-norms note is on every row and shown separately)."""
    return [w for w in warnings if not w.startswith("Bygger på ikke-verifiserte")]


class PlanTab(ttk.Frame):
    """Layout, top to bottom: header (title, version, the actions that apply now) · status strip
    (legal status, P check, warnings toggle) · field table · the selected field in three sub-tabs.
    Built to fit a small laptop screen (~800×450 for the tab)."""

    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=(8, 6))
        self.app = app
        self.plan: Plan | None = None
        self.view: PlanView | None = None
        self.selected_plan_id: int | None = None
        self.highlight: set[int] = set()  # field ids whose stage changed after "Kopier fra fjorår"
        self._previous_selection: tuple = ()
        self._context_key: tuple = ()
        self._warnings_open = False
        self._warning_count = 0
        self._serious = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        ttk.Style(self).configure("Warn.TButton", foreground=RED)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew")
        self.title_label = ttk.Label(header, text="", font=_BOLD)
        self.title_label.pack(side="left")
        self.version_var = tk.StringVar()
        self.version_combo = ttk.Combobox(header, textvariable=self.version_var, state="readonly", width=20)
        self.version_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_version_selected())
        self.action_frame = ttk.Frame(header)
        self.action_frame.pack(side="right")
        self.actions = {
            key: ttk.Button(self.action_frame, text=text, command=command)
            for key, text, command in (
                ("create", "Opprett plan", self.create_plan),
                ("copy", "Kopier fra fjorår", self.copy_last_year),
                ("recalc", "Beregn alle", self.recalculate),
                ("add_fields", "Legg til skifter", self.add_fields),
                ("finalize", "Ferdigstill…", self.finalize),
                ("new_version", "Ny versjon…", self.new_version),
                ("pdf", "Eksporter PDF", self.export_pdf),
            )
        }

        strip = ttk.Frame(self)
        strip.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        strip.columnconfigure(2, weight=1)
        self.legal_label = ttk.Label(strip, text="")
        self.legal_label.grid(row=0, column=0, sticky="w")
        self.p_label = ttk.Label(strip, text="")
        self.p_label.grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.warn_button = ttk.Button(strip, text="", command=self._toggle_warnings)

        self.warning_frame = ttk.Frame(self)
        self.warning_frame.columnconfigure(0, weight=1)
        self.warning_text = tk.Text(self.warning_frame, height=4, wrap="word", font="TkDefaultFont",
                                    relief="flat", borderwidth=0, padx=6, pady=4)
        ys = ttk.Scrollbar(self.warning_frame, orient="vertical", command=self.warning_text.yview)
        self.warning_text.configure(yscrollcommand=ys.set)
        self.warning_text.grid(row=0, column=0, sticky="ew")
        ys.grid(row=0, column=1, sticky="ns")
        for tag, colour in (("red", RED), ("amber", AMBER)):
            self.warning_text.tag_configure(tag, foreground=colour)

        panes = self.panes = ttk.PanedWindow(self, orient="vertical")
        panes.grid(row=3, column=0, sticky="nsew")
        panes.bind("<Configure>", lambda _e: self.after_idle(self._place_sash))

        tree_frame, self.tree = scrolled_tree(panes, [
            ("no", "Nr", 36), ("name", "Skifte", 130), ("culture", "Kultur", 90), ("area", "Daa", 50),
            ("req_n", "Behov N", 60), ("req_p", "Behov P", 60), ("req_k", "Behov K", 60),
            ("pl_n", "Plan N", 56), ("pl_p", "Plan P", 56), ("pl_k", "Plan K", 56),
            ("p_status", "P-grense", 64), ("remark", "Merknad", 170),
        ], height=6, numeric=("area", "req_n", "req_p", "req_k", "pl_n", "pl_p", "pl_k"), xscroll=True)
        _fixed_width(self.tree, keep_stretch=("name", "remark"))
        panes.add(tree_frame, weight=1)
        for tag, colour in (("p_over", RED), ("soil", AMBER), ("unfertilized", GREY)):
            self.tree.tag_configure(tag, foreground=colour)
        self.tree.tag_configure("stage_changed", background="#fff3c4")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())

        detail = self.detail = ttk.Frame(panes)
        panes.add(detail, weight=2)
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(1, weight=1)
        self.detail_label = ttk.Label(detail, text="", font=_BOLD)
        self.detail_label.grid(row=0, column=0, sticky="w", pady=(4, 2))
        self.sub = ttk.Notebook(detail)
        self.sub.grid(row=1, column=0, sticky="nsew")
        self.sub.add(self._build_fertilizing_tab(), text="Gjødsling")
        self.sub.add(self._build_season_tab(), text="Sesong")
        self.sub.add(self._build_calc_tab(), text="Beregning")

    def _build_fertilizing_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.sub, padding=(6, 4))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree_frame, self.app_tree = scrolled_tree(frame, [
            ("timing", "Tidspunkt", 140), ("product", "Produkt", 180), ("kg", "kg/daa", 60), ("n", "N", 52),
            ("p", "P", 52), ("k", "K", 52), ("total", "Totalt kg", 72), ("note", "Notat", 120),
        ], height=4, numeric=("kg", "n", "p", "k", "total"), xscroll=True)
        _fixed_width(self.app_tree, keep_stretch=("product", "note"))
        tree_frame.grid(row=0, column=0, sticky="nsew")
        self.app_tree.bind("<Double-1>", lambda _e: self.edit_application())
        # Buttons in a column to the right, the balance under the table: fits narrow windows.
        self.app_buttons = ttk.Frame(frame)
        self.app_buttons.grid(row=0, column=1, rowspan=2, sticky="n", padx=(8, 0))
        for text, command in (("Legg til", self.add_application), ("Rediger", self.edit_application),
                              ("Slett", self.delete_application), ("Bruk mal…", self.use_template)):
            ttk.Button(self.app_buttons, text=text, command=command).pack(fill="x", pady=(0, 2))
        self.balance_grid = _NpkGrid(frame, ("Behov", "Planlagt", "Balanse"), width=10)
        self.balance_grid.grid(row=1, column=0, sticky="w", pady=(6, 0))
        return frame

    def _build_season_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.sub, padding=(6, 4))
        f = self.season_form = Form(frame, columns=2)
        f.grid(row=0, column=0, sticky="nw")
        f.check("fertilized", "Gjødsles i år")
        f.entry("expected_yield_kg_daa", "Avling (kg/daa)", width=10)
        f.combo("growth_class", "Nyvekst", [], width=15)
        f.entry("forgrode", "Forgrøde", width=17)
        f.combo("soil_sample_id", "Jordprøve", [], width=20)
        f.entries("Overstyrt", [("override_n", "N"), ("override_p", "P"), ("override_k", "K")], width=5, column=1)
        f.entry("override_reason", "Begrunnelse", width=18, column=1)
        f.text("notes", "Notater", height=1, width=18, column=1)
        self.save_season_button = ttk.Button(f, text="Lagre sesong", command=self.save_season)
        f.custom("save", "", self.save_season_button, column=1)
        return frame

    def _build_calc_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.sub, padding=(6, 4))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        steps_frame, self.steps_tree = scrolled_tree(frame, [
            ("nutrient", "", 30), ("step", "Steg", 200), ("delta", "Endring", 64), ("after", "Etter", 56),
        ], height=6, numeric=("delta", "after"), xscroll=True)
        _fixed_width(self.steps_tree, keep_stretch=("step",))
        steps_frame.grid(row=0, column=0, sticky="nsew")
        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self.req_grid = _NpkGrid(right, ("Beregnet", "Overstyrt", "Gjeldende"), width=6)
        self.req_grid.grid(row=0, column=0, sticky="nw")
        text_frame = ttk.Frame(right)
        text_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.calc_text = tk.Text(text_frame, height=5, width=30, wrap="word", font="TkDefaultFont",
                                 relief="flat", borderwidth=0, padx=4, pady=2)
        ys = ttk.Scrollbar(text_frame, orient="vertical", command=self.calc_text.yview)
        self.calc_text.configure(yscrollcommand=ys.set)
        self.calc_text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self.calc_text.tag_configure("bold", font=_BOLD)
        self.calc_text.tag_configure("red", foreground=RED)
        self.calc_text.tag_configure("amber", foreground=AMBER)
        return frame

    def _place_sash(self) -> None:
        """Give the field detail the height it asks for; the field table keeps the rest (at least ~5 rows)."""
        total = self.panes.winfo_height()
        if total < 100:
            return
        floor = min(120, total // 2)
        self.panes.sashpos(0, max(total - self.detail.winfo_reqheight(), floor))

    # --- Loading -------------------------------------------------------------------

    def _context(self):
        """(ref, settings) or None with a message in the status strip."""
        if self.app.ref is None:
            self._set_message("Referansedata er ikke lastet – rett feilene i referansefilene og start på nytt.", RED)
            return None
        settings = self.app.settings()
        if settings is None:
            self._set_message("Innstillingene er ugyldige – se Innstillinger.", RED)
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
            self._show_versions([])
            self._show_actions(())
            self._set_message("")
            self._show_detail()
            return
        plans = repo.list_plans(self.app.conn, eid, year)
        self._show_versions(plans)
        if self.selected_plan_id not in {p.id for p in plans}:
            self.selected_plan_id = plans[-1].id if plans else None
        self.plan = next((p for p in plans if p.id == self.selected_plan_id), None)
        if self.plan is None:
            self.title_label.configure(text=f"Ingen plan for {year}")
            self._show_actions(("create", "copy"))
            self._set_message(f"Trykk «Opprett plan» eller «Kopier fra fjorår» for å lage planen for {year}.")
            self._show_detail()
            return
        self.version_var.set(self._version_label(self.plan))
        newest = self.plan.id == plans[-1].id
        state = "ferdigstilt" if self.plan.finished else "utkast"
        self.title_label.configure(
            text=f"Plan {year} · versjon {self.plan.version} · {state}" + ("" if newest else " (eldre versjon)"))
        if not self.plan.finished:
            self._show_actions(("recalc", "add_fields", "finalize", "pdf"))
        else:
            self._show_actions(("new_version", "pdf") if newest else ("pdf",))

        context = self._context()
        if context is None:
            return
        ref, settings = context
        try:
            self.view = ps.build_view(self.app.conn, ref, settings, self.plan)
        except ValidationError as exc:
            self._set_message(exc.errors[0], RED)
            return
        self._fill_tree()
        self._fill_status()
        self._show_detail()

    @staticmethod
    def _version_label(p: Plan) -> str:
        return f"Versjon {p.version} ({p.status})"

    def _show_versions(self, plans: list[Plan]) -> None:
        """The version selector is only shown when there is something to choose between."""
        self.version_combo.configure(values=[self._version_label(p) for p in plans])
        if len(plans) > 1:
            self.version_combo.pack(side="left", padx=(10, 0))
        else:
            self.version_combo.pack_forget()
            self.version_var.set("")

    def _show_actions(self, keys: tuple[str, ...]) -> None:
        for button in self.actions.values():
            button.pack_forget()
        for key in keys:
            self.actions[key].pack(side="left", padx=(6, 0))

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
                   for n in _NPK]
            planned = ([format_number(r.planned_supply[n], empty="?") for n in _NPK] if r.fertilized
                       else ["–"] * 3)
            if not r.fertilized or r.p_over_field_limit is None:
                p_status = "–"
            else:
                p_status = "OVER" if r.p_over_field_limit else "OK"
            self.tree.insert("", "end", iid=str(r.season_id), tags=tags, values=(
                r.field_no, r.name, r.culture_name, format_number(r.area_daa), *req, *planned, p_status,
                self._remark(r),
            ))
        stripe(self.tree, skip=("stage_changed",))
        if selection and self.tree.exists(selection[0]):
            self.tree.selection_set(selection[0])
        elif self.view.rows:
            self.tree.selection_set(str(self.view.rows[0].season_id))

    def _remark(self, r: FieldRow) -> str:
        """The most important things about a field, in words (full list under «Beregning»)."""
        if not r.fertilized:
            return "Ikke gjødslet"
        parts = []
        if r.field_id in self.highlight:
            parts.append("Nytt stadium")
        if r.p_over_field_limit:
            parts.append("P over grensen")
        if not r.soil_valid:
            parts.append("Mangler gyldig jordprøve")
        if any(v is not None for v in r.override.values()):
            parts.append("Overstyrt")
        others = [w for w in serious(r.warnings) if not w.startswith(NO_VALID_SOIL)]
        if others:
            parts.append(f"{len(others)} {'varsel' if len(others) == 1 else 'varsler'}")
        return ", ".join(parts)

    def _fill_status(self) -> None:
        v = self.view
        # Compact texts; the full wording is in the window's status bar and in the PDF.
        area = format_number(v.fertilized_area)
        legal = (f"Plan påkrevd · {area} daa gjødslet" if v.plan_required
                 else f"Plan ikke påkrevd · {area} daa gjødslet · journal påkrevd")
        self.legal_label.configure(text=legal, foreground="")
        if v.p_limit is None or v.p_average is None:
            p_text = "P-sjekk: " + ("ingen P-grense funnet" if v.p_limit is None else "planlagt P ukjent")
        else:
            p_text = (f"P-sjekk {'OK' if v.p_ok else 'BRUDD'}: snitt {format_number(v.p_average, 2)} av maks "
                      f"{format_number(v.p_limit)} kg/daa")
        self.p_label.configure(text=p_text, foreground=GREEN if v.p_ok else (RED if v.p_ok is False else AMBER))
        lines = []
        if v.change_reason:
            lines.append((f"Endringsbegrunnelse (versjon {v.version}): {v.change_reason}", ""))
        lines += [(f"⚠ {w}", "amber" if w.startswith(ps.UNVERIFIED_WARNING) else "red") for w in v.warnings]
        t = self.warning_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for text, tag in lines:
            t.insert("end", text + "\n", tag)
        t.configure(state="disabled")
        self._warning_count = len(v.warnings)
        self._serious = any(not w.startswith(ps.UNVERIFIED_WARNING) for w in v.warnings)
        self._update_warn_button()

    def _set_message(self, text: str, colour: str = "") -> None:
        """A single message in the status strip (no plan, or the plan can't be shown)."""
        self.legal_label.configure(text=text, foreground=colour)
        self.p_label.configure(text="")
        self._warning_count = 0
        self._warnings_open = False
        self._update_warn_button()

    def _toggle_warnings(self) -> None:
        self._warnings_open = not self._warnings_open
        self._update_warn_button()

    def _update_warn_button(self) -> None:
        if not self._warning_count:
            self.warn_button.grid_remove()
            self.warning_frame.grid_remove()
            return
        noun = "varsel" if self._warning_count == 1 else "varsler"
        self.warn_button.configure(
            text=f"Skjul {noun}" if self._warnings_open else f"⚠ {self._warning_count} {noun} – vis",
            style="Warn.TButton" if self._serious else "TButton")
        self.warn_button.grid(row=0, column=3, sticky="e")
        if self._warnings_open:
            self.warning_frame.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        else:
            self.warning_frame.grid_remove()

    def _selected_row(self) -> FieldRow | None:
        selection = self.tree.selection()
        if not selection or self.view is None:
            return None
        return self.view.row(int(selection[0]))

    # --- Detail panel -----------------------------------------------------------------

    def _show_detail(self) -> None:
        row = self._selected_row()
        editable = row is not None and self.plan is not None and not self.plan.finished
        if row is None:
            self.detail_label.configure(text="Velg et skifte i tabellen." if self.view else "")
        else:
            variety = f" / {row.variety}" if row.variety else ""
            self.detail_label.configure(
                text=f"Skifte {row.label} · {row.culture_name}{variety} · {format_number(row.area_daa)} daa · "
                     f"{row.stage or 'stadium ukjent'} · Jordprøve: {row.soil_sample_text}")
        self._fill_season_form(row)
        self.season_form.set_enabled(editable)
        self.save_season_button.state(["!disabled"] if editable else ["disabled"])
        for child in self.app_buttons.winfo_children():
            child.state(["!disabled"] if editable else ["disabled"])
        self._fill_calc(row)
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
        for n in _NPK:
            f.set(f"override_{n}", row.override[n])
        f.set("override_reason", row.override_reason)
        f.set("notes", row.notes)

    def _fill_calc(self, row: FieldRow | None) -> None:
        self.steps_tree.delete(*self.steps_tree.get_children())
        self.req_grid.clear()
        t = self.calc_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if row is not None:
            for i, s in enumerate(row.steps):
                delta = "" if s.kind in ("start", "note") else format_signed(s.delta)
                self.steps_tree.insert("", "end", iid=str(i), values=(
                    s.nutrient, s.label_nb, delta, format_number(s.value_after)))
            stripe(self.steps_tree)
            self.req_grid.set("Beregnet", {n: format_number(row.req[n], empty="–") for n in _NPK})
            self.req_grid.set("Overstyrt", {n: format_number(row.override[n], empty="") for n in _NPK})
            self.req_grid.set("Gjeldende", {n: format_number(row.effective[n], empty="–") for n in _NPK}, bold=True)
            if row.override_reason:
                t.insert("end", "Begrunnelse for overstyring: ", "bold")
                t.insert("end", row.override_reason + "\n\n")
            for w in row.warnings:
                t.insert("end", f"⚠ {w}\n", "red" if w in serious(row.warnings) else "amber")
            if row.soil_notes:
                t.insert("end", "\nJordstatus (påvirker ikke tallene)\n", "bold")
                for note in row.soil_notes:
                    t.insert("end", f"• {note}\n")
            if row.rationale:
                t.insert("end", "\nTekst i planen\n", "bold")
                t.insert("end", row.rationale + "\n")
        t.configure(state="disabled")

    def _fill_applications(self, row: FieldRow | None) -> None:
        self.app_tree.delete(*self.app_tree.get_children())
        self.balance_grid.clear()
        if row is None:
            return
        for line in row.planned:
            self.app_tree.insert("", "end", iid=str(line.id), values=_app_values(line))
        stripe(self.app_tree)
        self.balance_grid.set("Behov", {n: format_number(row.effective[n], empty="–") for n in _NPK})
        self.balance_grid.set("Planlagt", {n: format_number(row.planned_supply[n], empty="ukjent") for n in _NPK})
        texts, colours = {}, {}
        for n in _NPK:
            status = row.balance_status[n]
            if status is None:
                texts[n], colours[n] = "ukjent", GREY
            else:
                texts[n] = f"{format_signed(row.balance[n])} {_BALANCE_TEXT[status]}"
                colours[n] = _BALANCE_COLOURS[status]
        self.balance_grid.set("Balanse", texts, colours)

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


class _NpkGrid(ttk.Frame):
    """Small table of N, P and K values laid out with labels in a grid, so numbers line up exactly."""

    def __init__(self, master, rows: tuple[str, ...], width: int):
        super().__init__(master)
        ttk.Label(self, text="kg/daa", foreground="#666666").grid(row=0, column=0, sticky="w", padx=(0, 12))
        for c, name in enumerate("NPK", start=1):
            ttk.Label(self, text=name, font=_BOLD, anchor="e", width=width).grid(row=0, column=c, sticky="e")
        self.cells: dict[tuple[str, str], ttk.Label] = {}
        for r, name in enumerate(rows, start=1):
            ttk.Label(self, text=name).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=1)
            for c, n in enumerate(_NPK, start=1):
                cell = ttk.Label(self, text="", anchor="e", width=width)
                cell.grid(row=r, column=c, sticky="e", pady=1)
                self.cells[(name, n)] = cell

    def set(self, row: str, values: dict[str, str], colours: dict[str, str] | None = None,
            bold: bool = False) -> None:
        for n in _NPK:
            self.cells[(row, n)].configure(text=values.get(n, ""), foreground=(colours or {}).get(n, ""),
                                           font=_BOLD if bold else "TkDefaultFont")

    def clear(self) -> None:
        for cell in self.cells.values():
            cell.configure(text="", foreground="")


def _fixed_width(tree: ttk.Treeview, keep_stretch: tuple[str, ...]) -> None:
    """Only the text columns grow with the window; number columns keep their width."""
    for column in tree["columns"]:
        tree.column(column, stretch=column in keep_stretch)


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
