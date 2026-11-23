"""Jordprøver: soil samples of the selected enterprise, linked fields and status against optimum."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from .. import plan_service as ps
from .. import repo
from ..attachments import open_path, resolve, store_attachment
from ..models import SOIL_METHODS, SoilSample
from ..soil import P_AL_UNIT_P, P_AL_UNIT_P2O5, P_AL_UNITS, SOIL_PARAMETERS, classify, p_al_to_element, sample_validity
from ..units import format_date, format_plain, parse_date, round_half_up
from ..validation import ValidationError
from .coordinates import add_coordinate_picker, address_query
from .widgets import (
    AMBER, BLUE, GREEN, GREY, RED, Checklist, EditDialog, button_row, scrolled_tree, selected_id, show_errors,
)

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
            ("Velg skifter…", self.choose_fields),
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
                    format_date(s.sample_date), s.label or "", s.lab or "",
                    s.method + (" · utl." if s.origin == "utenlandsk" else ""), *values,
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
        dialog = SoilSampleDialog(self, self.app, s)
        if dialog.show():
            self.app.data_changed()
            self._offer_plan_update(dialog.saved_id)

    def choose_fields(self) -> None:
        """Use one sample (one lab analysis) for several fields."""
        s = self._selected_sample()
        if s and SampleFieldsDialog(self, self.app, s).show():
            self.app.data_changed()
            self._offer_plan_update(s.id)

    def _offer_plan_update(self, sample_id: int | None) -> None:
        """Draft plans keep the sample chosen when they were made; offer to switch the linked fields to this one."""
        settings = self.app.settings()
        if sample_id is None or settings is None or self.app.ref is None:
            return
        updates = ps.sample_plan_updates(self.app.conn, settings, sample_id)
        if not updates:
            return
        names = "\n".join(f"• Plan {plan.year}: skifte {f.field_no} {f.name}" for plan, _season, f in updates)
        if messagebox.askyesno(
            "Bruk prøven i planen",
            f"Skal jordprøven brukes i utkastet til gjødslingsplan for disse skiftene?\n\n{names}\n\n"
            "De har i dag ingen gyldig jordprøve eller en eldre prøve. Behovet beregnes på nytt.",
            parent=self,
        ):
            ps.use_sample_in_drafts(self.app.conn, self.app.ref, settings, sample_id, updates)
            self.app.data_changed()
            self.app.set_status(f"Jordprøven brukes nå for {len(updates)} skifte(r) i planen.")

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
        if s.origin == "utenlandsk" and not filename.lower().endswith(".pdf"):
            show_errors(self, ["Utenlandske jordprøver må ha rapporten som PDF (den legges ved planen)."], "Rapport")
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
        self._report_source: Path | None = None  # PDF picked in this dialog, copied on save
        f = self.form
        f.combo("origin", "Laboratorium *", [("norsk", "Norsk lab"), ("utenlandsk", "Utenlandsk lab")], width=16)
        f.var("origin").trace_add("write", lambda *_: self._update_report_hint(origin_changed=True))
        f.entry("sample_date", "Prøvedato * (dd.mm.åååå)", width=12)
        f.entry("label", "Merking (som på rapporten)")
        f.entry("lab", "Navn på lab")
        f.entry("lab_report_no", "Rapportnr.")
        report = ttk.Frame(f)
        self.report_name = ttk.Label(report, text="", width=24)
        self.report_name.pack(side="left")
        ttk.Button(report, text="Velg PDF…", command=self._choose_report).pack(side="left", padx=(6, 0))
        f.custom("report", "Analyserapport", report)
        self.report_hint = ttk.Label(f, text="", wraplength=300, justify="left", foreground="#666666")
        f.custom("report_hint", "", self.report_hint)
        f.combo("method", "Metode", [(m, m) for m in SOIL_METHODS], width=8)
        f.entry("depth_from_cm", "Dybde fra (cm)", width=8)
        f.entry("depth_to_cm", "Dybde til (cm)", width=8)
        f.entry("lat", "Breddegrad (WGS84)", width=14)
        f.entry("lon", "Lengdegrad (WGS84)", width=14)
        add_coordinate_picker(f, 0, self._map_fallback)
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
        self.report_name.configure(text=Path(sample.attachment).name if sample.attachment else "ingen valgt")
        f.set("origin", sample.origin)
        self._loaded = True

        self.saved_id: int | None = None
        ttk.Label(self.extra, text="Skifter prøven gjelder for – én prøve kan brukes for flere skifter:").pack(anchor="w")
        self._fields, self.field_list = _field_checklist(self.extra, app, sample)
        self.field_list.pack(fill="x", pady=(2, 0))

    def _update_report_hint(self, origin_changed: bool = False) -> None:
        foreign = self.form.get("origin") == "utenlandsk"
        self.report_hint.configure(
            text="Påkrevd for utenlandsk lab: PDF-en legges ved bakerst i gjødslingsplanen. Verdiene brukes bare i "
                 "beregningen hvis metoden er AL." if foreign else "Valgfritt for norsk lab (legges ikke ved planen).",
            foreground=AMBER if foreign and not (self._report_source or self.sample.attachment) else "#666666")
        if origin_changed and foreign and getattr(self, "_loaded", False) and self.form.get("method") == "AL":
            self.form.set("method", "annet")  # foreign labs rarely use the Norwegian AL method; change back if they do

    def _choose_report(self) -> None:
        name = filedialog.askopenfilename(parent=self, title="Velg analyserapport (PDF)",
                                          filetypes=[("PDF", "*.pdf"), ("Alle filer", "*.*")])
        if not name:
            return
        if not name.lower().endswith(".pdf"):
            show_errors(self, ["Analyserapporten må være en PDF-fil."], "Analyserapport")
            return
        self._report_source = Path(name)
        self.report_name.configure(text=self._report_source.name)
        self._update_report_hint()

    def _map_fallback(self):
        """Open Google Maps at a field the sample covers (ticked first), else the farm address."""
        ticked = set(self.field_list.selected())
        for fl in [fl for fl in self._fields if fl.id in ticked] + self._fields:
            if fl.lat is not None and fl.lon is not None:
                return fl.lat, fl.lon
        return address_query(repo.get_enterprise(self.app.conn, self.sample.enterprise_id))

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
        values.update(notes=notes, sample_date=sampled.isoformat() if sampled else "", p_al=p_al,
                      origin=f.get("origin"))
        if values["origin"] == "utenlandsk" and not (self._report_source or
                                                     (self.sample.attachment or "").lower().endswith(".pdf")):
            raise ValidationError("Velg analyserapporten (PDF) for en utenlandsk jordprøve.")
        sample = replace(self.sample, **values)
        field_ids = self.field_list.selected()
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
        if self._report_source is not None:
            sample = replace(sample, attachment=store_attachment(
                self.app.paths.data_dir, sample.enterprise_id, self._report_source))
        self.saved_id = repo.save_soil_sample(self.app.conn, sample, field_ids).id
        return None


class SampleFieldsDialog(EditDialog):
    """Pick the fields one soil sample covers, without opening the full sample form."""

    def __init__(self, master, app: MainWindow, sample: SoilSample):
        super().__init__(master, "Skifter for jordprøven")
        self.app = app
        self.sample = sample
        name = f"{format_date(sample.sample_date)} {sample.label or ''}".strip()
        ttk.Label(self.form, text=f"Jordprøve {name}", font=("TkDefaultFont", 0, "bold")).pack(anchor="w")
        ttk.Label(self.form, text="Prøveresultatet brukes for alle skiftene du krysser av.",
                  foreground="#666666").pack(anchor="w", pady=(0, 6))
        self._fields, self.field_list = _field_checklist(self.form, app, sample)
        self.field_list.pack(fill="x")

    def save(self) -> bool | None:
        field_ids = self.field_list.selected()
        if not field_ids and not messagebox.askyesno(
            "Ingen skifter", "Prøven blir ikke knyttet til noe skifte og blir ikke brukt i planer.\n\nLagre likevel?",
            parent=self,
        ):
            return False
        repo.set_sample_fields(self.app.conn, self.sample.id, field_ids)
        return None


def _field_checklist(master, app: MainWindow, sample: SoilSample):
    """Active fields (plus inactive ones already linked) as check boxes, the linked ones ticked."""
    linked = set()
    if sample.id is not None:
        linked = set(repo.sample_field_links(app.conn, sample.enterprise_id).get(sample.id, []))
    fields = [fl for fl in repo.list_fields(app.conn, sample.enterprise_id) if fl.active or fl.id in linked]
    cultures = {c.code: c.name_nb for c in app.ref.cultures.values()} if app.ref else {}
    items = [(fl.id, f"{fl.field_no} – {fl.name} ({cultures.get(fl.culture_code, fl.culture_code)}, "
                     f"{format_plain(fl.area_daa)} daa)" + ("" if fl.active else " – inaktiv")) for fl in fields]
    return fields, Checklist(master, items, linked)
