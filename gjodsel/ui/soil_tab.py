"""Jordprøver: soil samples of the selected enterprise, linked fields and status against optimum."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from .. import repo
from ..attachments import open_path, resolve, store_attachment
from ..models import SOIL_METHODS, SoilSample
from ..soil import P_AL_UNIT_P, P_AL_UNIT_P2O5, P_AL_UNITS, SOIL_PARAMETERS, classify, p_al_to_element, sample_validity
from ..units import format_date, format_plain, parse_date, round_half_up
from ..validation import ValidationError
from .widgets import AMBER, BLUE, GREEN, GREY, RED, EditDialog, button_row, scrolled_tree, selected_id, show_errors

if TYPE_CHECKING:
    from .main_window import MainWindow

REPORT_FILETYPES = [("Rapport (PDF eller bilde)", "*.pdf *.png *.jpg *.jpeg"), ("Alle filer", "*.*")]
_MARKERS = {"lav": " ▼", "høy": " ▲", "ok": "", None: ""}
_STATUS_COLOURS = {"lav": AMBER, "høy": BLUE, "ok": GREEN}
_VALUE_COLUMNS = ("ph", "p_al", "k_al", "mg_al", "ca_al", "k_hno3", "mold_pct")


class SoilTab(ttk.Frame):
    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=8)
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        frame, self.tree = scrolled_tree(self, [
            ("date", "Dato", 85), ("label", "Merking", 110), ("lab", "Lab", 90), ("method", "Metode", 55),
            ("ph", "pH", 55), ("p_al", "P-AL", 60), ("k_al", "K-AL", 60), ("mg_al", "Mg-AL", 60),
            ("ca_al", "Ca-AL", 65), ("k_hno3", "K-HNO₃", 65), ("mold_pct", "Mold %", 60),
            ("fields", "Skifter", 110), ("coords", "Koord.", 70), ("status", "Status", 200),
            ("attachment", "Rapport", 60),
        ], height=12, numeric=_VALUE_COLUMNS)
        frame.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure("invalid", foreground=GREY)
        self.tree.tag_configure("soon", foreground=AMBER)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())
        self.tree.bind("<Double-1>", lambda _e: self.edit())

        button_row(self, [
            ("Ny jordprøve", self.new),
            ("Rediger", self.edit),
            ("Slett", self.delete),
            ("Legg ved rapport…", self.attach_report),
            ("Åpne rapport", self.open_report),
        ]).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            self, text="▼ under optimum   ▲ over optimum   (optimum fra soil_optimum.csv; P-AL lagres alltid "
                       "som mg P/100 g)", foreground="#666666",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))

        self.detail = ttk.LabelFrame(self, text="Valgt jordprøve mot optimum", padding=8)
        self.detail.grid(row=3, column=0, sticky="ew", pady=(8, 0))

    # --- Data -------------------------------------------------------------------

    def _max_age(self) -> int | None:
        try:
            return repo.load_settings(self.app.conn).soil_max_age_years
        except ValidationError:
            return None

    def _optimum(self) -> dict:
        return self.app.ref.soil_optimum if self.app.ref else {}

    def refresh(self) -> None:
        selection = selected_id(self.tree)
        self.tree.delete(*self.tree.get_children())
        self.tree.heading("status", text=f"Status for {self.app.year}")
        eid = self.app.enterprise_id
        if eid is not None:
            fields = {f.id: f for f in repo.list_fields(self.app.conn, eid)}
            links = repo.sample_field_links(self.app.conn, eid)
            max_age = self._max_age()
            optimum = self._optimum()
            for s in repo.list_soil_samples(self.app.conn, eid):
                status, tag = self._status(s, max_age)
                field_nos = sorted((fields[fid].field_no for fid in links.get(s.id, []) if fid in fields),
                                   key=lambda no: (len(no), no))
                values = []
                for key in _VALUE_COLUMNS:
                    value = getattr(s, key)
                    text = format_plain(round_half_up(value, 1)) if value is not None else ""
                    values.append(text + _MARKERS[classify(value, optimum.get(key))])
                self.tree.insert("", "end", iid=str(s.id), tags=(tag,) if tag else (), values=(
                    format_date(s.sample_date), s.label or "", s.lab or "", s.method, *values,
                    ", ".join(field_nos) or "ingen", "ok" if s.lat is not None else "mangler",
                    status, "ja" if s.attachment else "",
                ))
        if selection is not None and self.tree.exists(str(selection)):
            self.tree.selection_set(str(selection))
        self._show_detail()

    def _status(self, s: SoilSample, max_age: int | None) -> tuple[str, str]:
        if max_age is None:
            return "ukjent (sjekk innstillinger)", ""
        v = sample_validity(s.method, s.year, self.app.year, max_age)
        if not v.valid:
            return f"ikke gyldig – {v.reason}", "invalid"
        if v.expires_soon:
            return f"gyldig, utløper snart ({v.age} år)", "soon"
        return f"gyldig ({v.age} år)", ""

    def _show_detail(self) -> None:
        for child in self.detail.winfo_children():
            child.destroy()
        sid = selected_id(self.tree)
        s = repo.get_soil_sample(self.app.conn, sid) if sid is not None else None
        if s is None:
            ttk.Label(self.detail, text="Velg en jordprøve i listen.").grid(row=0, column=0, sticky="w")
            return
        optimum = self._optimum()
        for col, (key, label) in enumerate(SOIL_PARAMETERS.items()):
            value = getattr(s, key)
            opt = optimum.get(key)
            status = classify(value, opt)
            text = f"{label}: {format_plain(round_half_up(value, 1)) if value is not None else '–'}"
            if opt is not None:
                text += f"\n(optimum {format_plain(opt.min)}–{format_plain(opt.max)})"
            if status:
                text += f"\n{status}"
            colour = {"fg": _STATUS_COLOURS[status]} if status in _STATUS_COLOURS else {}
            tk.Label(self.detail, text=text, justify="left", **colour).grid(
                row=0, column=col, sticky="nw", padx=(0, 24))
        notes = []
        if s.method != "AL":
            notes.append("Metode «annet»: verdiene lagres, men brukes ikke i beregningen.")
        if s.lat is None:
            notes.append("Mangler koordinater for prøvepunktet (§ 29).")
        for row, note in enumerate(notes, start=1):
            tk.Label(self.detail, text=note, fg=RED).grid(row=row, column=0, columnspan=5, sticky="w")

    def _selected_sample(self) -> SoilSample | None:
        sid = selected_id(self.tree)
        if sid is None:
            messagebox.showinfo("Jordprøve", "Velg en jordprøve i listen først.", parent=self)
            return None
        return repo.get_soil_sample(self.app.conn, sid)

    # --- Actions ----------------------------------------------------------------

    def new(self) -> None:
        if self.app.enterprise_id is None:
            messagebox.showinfo("Foretak", "Velg eller opprett et foretak først.", parent=self)
            return
        self._edit(SoilSample(enterprise_id=self.app.enterprise_id))

    def edit(self) -> None:
        s = self._selected_sample()
        if s:
            self._edit(s)

    def _edit(self, s: SoilSample) -> None:
        if SoilSampleDialog(self, self.app, s).show():
            self.app.data_changed()

    def delete(self) -> None:
        s = self._selected_sample()
        if not s:
            return
        if repo.soil_sample_in_use(self.app.conn, s.id):
            show_errors(self, ["Jordprøven er brukt i en gjødslingsplan og kan ikke slettes."], "Kan ikke slette")
            return
        if not messagebox.askyesno("Slett jordprøve", f"Slette jordprøven fra {format_date(s.sample_date)}?",
                                   icon="warning", parent=self):
            return
        repo.delete_soil_sample(self.app.conn, s.id)
        self.app.data_changed()

    def attach_report(self) -> None:
        s = self._selected_sample()
        if not s:
            return
        if s.attachment and not messagebox.askyesno(
            "Rapport", "Prøven har allerede en rapport. Erstatte den?\n"
            "(Den gamle filen blir liggende i vedleggsmappen.)", parent=self,
        ):
            return
        filename = filedialog.askopenfilename(parent=self, title="Velg analyserapport", filetypes=REPORT_FILETYPES)
        if not filename:
            return
        relative = store_attachment(self.app.paths.data_dir, s.enterprise_id, Path(filename))
        repo.set_soil_sample_attachment(self.app.conn, s.id, relative)
        self.app.data_changed()
        self.app.set_status(f"Rapport lagret som {relative}.")

    def open_report(self) -> None:
        s = self._selected_sample()
        if not s:
            return
        if not s.attachment:
            messagebox.showinfo("Rapport", "Prøven har ingen rapport vedlagt.", parent=self)
            return
        path = resolve(self.app.paths.data_dir, s.attachment)
        if not path.exists():
            show_errors(self, [f"Finner ikke filen {path}."], "Rapport")
            return
        open_path(path)


class SoilSampleDialog(EditDialog):
    _TEXT_KEYS = ("label", "lab", "lab_report_no", "method", "soil_type", "clay_class", "notes")
    _FLOAT_KEYS = ("lat", "lon", "ph", "k_al", "mg_al", "ca_al", "k_hno3", "mold_pct")
    _INT_KEYS = ("depth_from_cm", "depth_to_cm")

    def __init__(self, master, app: MainWindow, sample: SoilSample):
        super().__init__(master, "Ny jordprøve" if sample.id is None else "Rediger jordprøve", columns=2)
        self.app = app
        self.sample = sample
        f = self.form
        f.entry("sample_date", "Prøvedato * (dd.mm.åååå)", width=12)
        f.entry("label", "Merking (som på rapporten)")
        f.entry("lab", "Laboratorium")
        f.entry("lab_report_no", "Rapportnr.")
        f.combo("method", "Metode", [(m, m) for m in SOIL_METHODS], width=8)
        f.entry("depth_from_cm", "Dybde fra (cm)", width=8)
        f.entry("depth_to_cm", "Dybde til (cm)", width=8)
        f.entry("lat", "Breddegrad (WGS84)", width=14)
        f.entry("lon", "Lengdegrad (WGS84)", width=14)
        f.entry("soil_type", "Jordart")
        f.entry("clay_class", "Leirklasse")
        f.entry("ph", "pH", width=8, column=1)
        f.entry("p_al", "P-AL", width=8, column=1)
        f.combo("p_al_unit", "P-AL oppgitt som", [(u, u) for u in P_AL_UNITS], width=16, column=1)
        f.entry("k_al", "K-AL (mg/100 g)", width=8, column=1)
        f.entry("mg_al", "Mg-AL (mg/100 g)", width=8, column=1)
        f.entry("ca_al", "Ca-AL (mg/100 g)", width=8, column=1)
        f.entry("k_hno3", "K-HNO₃ (mg/100 g)", width=8, column=1)
        f.entry("mold_pct", "Moldinnhold (%)", width=8, column=1)
        f.text("notes", "Notater", width=30, height=4, column=1)

        for key in self._TEXT_KEYS + self._FLOAT_KEYS + self._INT_KEYS + ("p_al",):
            f.set(key, getattr(sample, key))
        f.set("sample_date", format_date(sample.sample_date))
        f.set("p_al_unit", P_AL_UNIT_P)

        ttk.Label(self.extra, text="Skifter prøven gjelder for (velg ett eller flere):").pack(anchor="w")
        linked = set()
        if sample.id is not None:
            linked = set(repo.sample_field_links(app.conn, sample.enterprise_id).get(sample.id, []))
        self._fields = [fl for fl in repo.list_fields(app.conn, sample.enterprise_id)
                        if fl.active or fl.id in linked]
        self.field_list = tk.Listbox(self.extra, selectmode="multiple", height=6, exportselection=False)
        self.field_list.pack(fill="x")
        for index, fl in enumerate(self._fields):
            self.field_list.insert("end", f"{fl.field_no} – {fl.name}" + ("" if fl.active else " (inaktiv)"))
            if fl.id in linked:
                self.field_list.selection_set(index)

    def save(self) -> bool | None:
        f = self.form
        errors: list[str] = []
        try:
            sampled = parse_date(f.get("sample_date"))
        except ValueError:
            sampled = None
            errors.append(f"«Prøvedato»: «{f.get('sample_date')}» er ikke en gyldig dato.")
        entered_p = f.get_number("p_al", errors)
        unit = f.get("p_al_unit")
        values = {key: f.get_number(key, errors) for key in self._FLOAT_KEYS}
        values.update({key: f.get_number(key, errors, integer=True) for key in self._INT_KEYS})
        if errors:
            raise ValidationError(errors)

        notes = f.get("notes")
        p_al = p_al_to_element(entered_p, unit)
        if unit == P_AL_UNIT_P2O5 and entered_p is not None:
            line = (f"P-AL registrert som {format_plain(entered_p)} {P_AL_UNIT_P2O5}, "
                    f"omregnet til {format_plain(p_al)} {P_AL_UNIT_P}.")
            notes = f"{notes}\n{line}".strip()

        values.update({key: f.get(key) for key in self._TEXT_KEYS})
        values.update(notes=notes, sample_date=sampled.isoformat() if sampled else "", p_al=p_al)
        sample = replace(self.sample, **values)
        field_ids = [self._fields[i].id for i in self.field_list.curselection()]
        if not field_ids and not messagebox.askyesno(
            "Ingen skifter", "Prøven er ikke knyttet til noe skifte og blir ikke brukt i planer.\n\nLagre likevel?",
            parent=self,
        ):
            return False
        if sample.lat is None and not messagebox.askyesno(
            "Koordinater mangler", "§ 29 krever koordinater for prøvepunktet.\n\nLagre uten koordinater?",
            parent=self,
        ):
            return False
        repo.save_soil_sample(self.app.conn, sample, field_ids)
        return None
