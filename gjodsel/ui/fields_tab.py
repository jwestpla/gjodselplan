"""Eiendommer og skifter: properties (with map sketch) and fields of the selected enterprise."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from .. import repo
from ..attachments import open_path, resolve, store_attachment
from ..models import COVER_TYPES, IRRIGATION_TYPES, Field, Property, trees_per_daa
from ..units import format_number, parse_number
from ..validation import ValidationError
from .widgets import GREY, RED, EditDialog, button_row, scrolled_tree, selected_id, show_errors

if TYPE_CHECKING:
    from .main_window import MainWindow

GARDSKART_URL = "https://gardskart.nibio.no/search"
NORGESKART_URL = "https://norgeskart.no/"
GOOGLE_MAPS_URL = "https://www.google.com/maps?q={lat},{lon}"

MAP_FILETYPES = [("Kart (bilde eller PDF)", "*.png *.jpg *.jpeg *.pdf"), ("Alle filer", "*.*")]


class FieldsTab(ttk.Frame):
    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=8)
        self.app = app
        self.show_inactive = tk.BooleanVar(value=True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=2)

        prop_box = ttk.LabelFrame(self, text="Eiendommer", padding=6)
        prop_box.grid(row=0, column=0, sticky="nsew")
        prop_box.columnconfigure(0, weight=1)
        prop_box.rowconfigure(0, weight=1)
        frame, self.prop_tree = scrolled_tree(prop_box, [
            ("label", "Gårdsnavn", 170), ("kommunenr", "Knr.", 60), ("gnr", "Gnr.", 50), ("bnr", "Bnr.", 50),
            ("fnr", "Fnr.", 50), ("snr", "Snr.", 50), ("tenure", "Eid/leid", 70), ("map", "Kartskisse", 260),
        ], height=5, numeric=("gnr", "bnr", "fnr", "snr"))
        frame.grid(row=0, column=0, sticky="nsew")
        self.prop_tree.tag_configure("missing_map", foreground=RED)
        self.prop_tree.bind("<Double-1>", lambda _e: self.edit_property())
        button_row(prop_box, [
            ("Ny eiendom", self.new_property),
            ("Rediger", self.edit_property),
            ("Slett", self.delete_property),
            ("Legg ved kart…", self.attach_map),
            ("Åpne kart", self.open_map),
            ("Åpne i Gårdskart", self.open_gardskart),
        ]).grid(row=1, column=0, sticky="w", pady=(6, 0))

        field_box = ttk.LabelFrame(self, text="Skifter", padding=6)
        field_box.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        field_box.columnconfigure(0, weight=1)
        field_box.rowconfigure(0, weight=1)
        frame, self.field_tree = scrolled_tree(field_box, [
            ("field_no", "Nr.", 50), ("name", "Navn", 150), ("culture", "Kultur", 140), ("variety", "Sort", 110),
            ("area", "Daa", 60), ("planting_year", "Planteår", 70), ("trees", "Trær/daa", 70),
            ("property", "Eiendom", 170), ("active", "Aktiv", 50),
        ], height=10, numeric=("area", "planting_year", "trees"))
        frame.grid(row=0, column=0, sticky="nsew")
        self.field_tree.tag_configure("inactive", foreground=GREY)
        self.field_tree.bind("<Double-1>", lambda _e: self.edit_field())
        bottom = ttk.Frame(field_box)
        bottom.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        button_row(bottom, [
            ("Nytt skifte", self.new_field),
            ("Rediger", self.edit_field),
            ("Slett", self.delete_field),
            ("Aktiver/deaktiver", self.toggle_field_active),
            ("Vis i kart", self.open_google_maps),
            ("Norgeskart", self.open_norgeskart),
        ]).pack(side="left")
        ttk.Checkbutton(bottom, text="Vis inaktive", variable=self.show_inactive,
                        command=self.refresh).pack(side="left", padx=12)
        self.summary_label = ttk.Label(bottom, text="")
        self.summary_label.pack(side="right")

    # --- Data -------------------------------------------------------------------

    def refresh(self) -> None:
        prop_sel = selected_id(self.prop_tree)
        field_sel = selected_id(self.field_tree)
        self.prop_tree.delete(*self.prop_tree.get_children())
        self.field_tree.delete(*self.field_tree.get_children())
        self.summary_label.configure(text="")
        eid = self.app.enterprise_id
        if eid is None:
            return

        properties = {p.id: p for p in repo.list_properties(self.app.conn, eid)}
        for p in properties.values():
            missing = not p.map_attachment
            self.prop_tree.insert("", "end", iid=str(p.id), tags=("missing_map",) if missing else (), values=(
                p.farm_name or "", p.kommunenr, p.gnr, p.bnr, p.fnr, p.snr, p.tenure,
                "MANGLER – påkrevd etter § 26" if missing else Path(p.map_attachment).name,
            ))

        cultures = self.app.ref.cultures if self.app.ref else {}
        active_count, active_area = 0, 0.0
        for f in repo.list_fields(self.app.conn, eid):
            if f.active:
                active_count += 1
                active_area += f.area_daa
            elif not self.show_inactive.get():
                continue
            culture = cultures.get(f.culture_code)
            prop = properties.get(f.property_id)
            self.field_tree.insert("", "end", iid=str(f.id), tags=() if f.active else ("inactive",), values=(
                f.field_no, f.name, culture.name_nb if culture else f.culture_code, f.variety or "",
                format_number(f.area_daa), f.planting_year or "", format_number(f.trees_per_daa, 0),
                prop.label if prop else "", "ja" if f.active else "nei",
            ))
        self.summary_label.configure(text=f"Aktive skifter: {active_count} · {format_number(active_area)} daa")

        for tree, iid in ((self.prop_tree, prop_sel), (self.field_tree, field_sel)):
            if iid is not None and tree.exists(str(iid)):
                tree.selection_set(str(iid))

    def _selected_property(self) -> Property | None:
        pid = selected_id(self.prop_tree)
        if pid is None:
            messagebox.showinfo("Eiendom", "Velg en eiendom i listen først.", parent=self)
            return None
        return repo.get_property(self.app.conn, pid)

    def _selected_field(self) -> Field | None:
        fid = selected_id(self.field_tree)
        if fid is None:
            messagebox.showinfo("Skifte", "Velg et skifte i listen først.", parent=self)
            return None
        return repo.get_field(self.app.conn, fid)

    def _require_enterprise(self) -> bool:
        if self.app.enterprise_id is None:
            messagebox.showinfo("Foretak", "Velg eller opprett et foretak først.", parent=self)
            return False
        return True

    # --- Property actions ----------------------------------------------------------

    def new_property(self) -> None:
        if self._require_enterprise():
            self._edit_property(Property(enterprise_id=self.app.enterprise_id))

    def edit_property(self) -> None:
        p = self._selected_property()
        if p:
            self._edit_property(p)

    def _edit_property(self, p: Property) -> None:
        if PropertyDialog(self, self.app, p).show():
            self.app.data_changed()

    def delete_property(self) -> None:
        p = self._selected_property()
        if not p:
            return
        if repo.property_has_plans(self.app.conn, p.id):
            show_errors(self, ["Eiendommen har skifter som inngår i gjødslingsplaner og kan ikke slettes. "
                               "Deaktiver skiftene i stedet."], "Kan ikke slette")
            return
        n_fields = repo.property_field_count(self.app.conn, p.id)
        message = f"Slette eiendommen {p.label}?"
        if n_fields:
            message += f"\n\nDette sletter også {n_fields} skifte(r) på eiendommen."
        if not messagebox.askyesno("Slett eiendom", message, icon="warning", parent=self):
            return
        try:
            repo.delete_property(self.app.conn, p.id)
        except ValidationError as exc:
            show_errors(self, exc.errors, "Kan ikke slette")
            return
        self.app.data_changed()

    def attach_map(self) -> None:
        p = self._selected_property()
        if not p:
            return
        if p.map_attachment and not messagebox.askyesno(
            "Kartskisse", "Eiendommen har allerede et kart. Erstatte det?\n"
            "(Den gamle filen blir liggende i vedleggsmappen.)", parent=self,
        ):
            return
        filename = filedialog.askopenfilename(parent=self, title="Velg kartskisse", filetypes=MAP_FILETYPES)
        if not filename:
            return
        relative = store_attachment(self.app.paths.data_dir, p.enterprise_id, Path(filename))
        repo.set_property_map(self.app.conn, p.id, relative)
        self.app.data_changed()
        self.app.set_status(f"Kart lagret som {relative}.")

    def open_map(self) -> None:
        p = self._selected_property()
        if not p:
            return
        if not p.map_attachment:
            messagebox.showinfo("Kartskisse", "Eiendommen har ikke noe kart ennå.", parent=self)
            return
        path = resolve(self.app.paths.data_dir, p.map_attachment)
        if not path.exists():
            show_errors(self, [f"Finner ikke filen {path}."], "Kartskisse")
            return
        open_path(path)

    def open_gardskart(self) -> None:
        p = self._selected_property()
        if not p:
            return
        text = f"{p.kommunenr}/{p.gnr}/{p.bnr}/{p.fnr}/{p.snr}"
        self.clipboard_clear()
        self.clipboard_append(text)
        webbrowser.open(GARDSKART_URL)
        self.app.set_status(f"Kopierte {text} til utklippstavlen – lim inn i søket i Gårdskart.")

    # --- Field actions -------------------------------------------------------------------

    def new_field(self) -> None:
        if not self._require_enterprise():
            return
        properties = repo.list_properties(self.app.conn, self.app.enterprise_id)
        if not properties:
            messagebox.showinfo("Skifte", "Registrer en eiendom før du legger inn skifter.", parent=self)
            return
        default_property = selected_id(self.prop_tree) or properties[0].id
        self._edit_field(Field(property_id=default_property))

    def edit_field(self) -> None:
        f = self._selected_field()
        if f:
            self._edit_field(f)

    def _edit_field(self, f: Field) -> None:
        if FieldDialog(self, self.app, f).show():
            self.app.data_changed()

    def delete_field(self) -> None:
        f = self._selected_field()
        if not f:
            return
        if repo.field_has_plans(self.app.conn, f.id):
            if messagebox.askyesno(
                "Kan ikke slette",
                f"Skifte {f.field_no} {f.name} inngår i en gjødslingsplan og kan ikke slettes.\n\n"
                "Vil du deaktivere det i stedet?", parent=self,
            ):
                repo.set_field_active(self.app.conn, f.id, False)
                self.app.data_changed()
            return
        if not messagebox.askyesno("Slett skifte", f"Slette skifte {f.field_no} {f.name}?",
                                   icon="warning", parent=self):
            return
        try:
            repo.delete_field(self.app.conn, f.id)
        except ValidationError as exc:
            show_errors(self, exc.errors, "Kan ikke slette")
            return
        self.app.data_changed()

    def toggle_field_active(self) -> None:
        f = self._selected_field()
        if f:
            repo.set_field_active(self.app.conn, f.id, not f.active)
            self.app.data_changed()

    def _field_with_coordinates(self) -> Field | None:
        f = self._selected_field()
        if f and (f.lat is None or f.lon is None):
            messagebox.showinfo("Kart", "Skiftet har ikke koordinater. Legg dem inn under Rediger.", parent=self)
            return None
        return f

    def open_google_maps(self) -> None:
        f = self._field_with_coordinates()
        if f:
            webbrowser.open(GOOGLE_MAPS_URL.format(lat=f.lat, lon=f.lon))

    def open_norgeskart(self) -> None:
        f = self._field_with_coordinates()
        if f:
            text = f"{f.lat}, {f.lon}"
            self.clipboard_clear()
            self.clipboard_append(text)
            webbrowser.open(NORGESKART_URL)
            self.app.set_status(f"Kopierte koordinatene {text} til utklippstavlen.")


class PropertyDialog(EditDialog):
    _KEYS = ("farm_name", "kommunenr", "gnr", "bnr", "fnr", "snr", "tenure", "notes")

    def __init__(self, master, app: MainWindow, prop: Property):
        super().__init__(master, "Ny eiendom" if prop.id is None else "Rediger eiendom")
        self.app = app
        self.prop = prop
        f = self.form
        f.entry("farm_name", "Gårdsnavn")
        f.entry("kommunenr", "Kommunenr. *", width=8)
        f.entry("gnr", "Gnr. *", width=8)
        f.entry("bnr", "Bnr. *", width=8)
        f.entry("fnr", "Fnr.", width=8)
        f.entry("snr", "Snr.", width=8)
        f.combo("tenure", "Eid/leid", [("eid", "Eid"), ("leid", "Leid")], width=8)
        f.text("notes", "Notater", width=34)
        for key in self._KEYS:
            f.set(key, getattr(prop, key))

    def save(self) -> None:
        f = self.form
        errors: list[str] = []
        prop = replace(
            self.prop,
            farm_name=f.get("farm_name"),
            kommunenr=f.get("kommunenr"),
            gnr=f.get_number("gnr", errors, integer=True),
            bnr=f.get_number("bnr", errors, integer=True),
            fnr=f.get_number("fnr", errors, integer=True) or 0,
            snr=f.get_number("snr", errors, integer=True) or 0,
            tenure=f.get("tenure"),
            notes=f.get("notes"),
        )
        if errors:
            raise ValidationError(errors)
        repo.save_property(self.app.conn, prop)


class FieldDialog(EditDialog):
    _TEXT_KEYS = ("field_no", "name", "culture_code", "variety", "rootstock", "irrigation", "cover", "notes")
    _NUMBER_KEYS = ("area_daa", "planting_year", "row_spacing_m", "plant_spacing_m", "lat", "lon")

    def __init__(self, master, app: MainWindow, field: Field):
        super().__init__(master, "Nytt skifte" if field.id is None else f"Rediger skifte {field.field_no}",
                         columns=2)
        self.app = app
        self.field = field
        properties = repo.list_properties(app.conn, app.enterprise_id)
        if app.ref is not None:
            cultures = [(c.code, c.name_nb) for c in app.ref.cultures.values()]
        else:
            cultures = [(field.culture_code, field.culture_code)] if field.culture_code else []

        f = self.form
        f.entry("field_no", "Skiftenr. *", width=10)
        f.entry("name", "Navn *")
        f.combo("property_id", "Eiendom *", [(str(p.id), p.label) for p in properties])
        f.entry("area_daa", "Areal (daa) *", width=10)
        f.combo("culture_code", "Kultur *", cultures)
        f.entry("variety", "Sort")
        f.entry("rootstock", "Grunnstamme")
        f.entry("planting_year", "Planteår", width=8)
        f.check("active", "Aktiv")
        f.entry("row_spacing_m", "Radavstand (m)", width=8, column=1)
        f.entry("plant_spacing_m", "Planteavstand (m)", width=8, column=1)
        f.label("trees", "Trær/daa", column=1)
        f.entry("lat", "Breddegrad (WGS84)", width=14, column=1)
        f.entry("lon", "Lengdegrad (WGS84)", width=14, column=1)
        f.combo("irrigation", "Vanning", [(v, v) for v in IRRIGATION_TYPES], width=16, column=1)
        f.combo("cover", "Dekke", [(v, v) for v in COVER_TYPES], width=16, column=1)
        f.text("notes", "Notater", width=28, column=1)

        for key in self._TEXT_KEYS + self._NUMBER_KEYS:
            f.set(key, getattr(field, key))
        f.set("property_id", str(field.property_id) if field.property_id else "")
        f.set("active", field.active)
        for key in ("row_spacing_m", "plant_spacing_m"):
            f.var(key).trace_add("write", lambda *_: self._update_trees())
        self._update_trees()

    def _update_trees(self) -> None:
        try:
            row = parse_number(self.form.get("row_spacing_m"))
            plant = parse_number(self.form.get("plant_spacing_m"))
        except ValueError:
            row = plant = None
        trees = trees_per_daa(row, plant)
        self.form.set("trees", format_number(trees, 0) if trees else "–")

    def save(self) -> None:
        f = self.form
        errors: list[str] = []
        prop = f.get("property_id")
        field = replace(
            self.field,
            **{key: f.get(key) for key in self._TEXT_KEYS},
            property_id=int(prop) if prop else None,
            area_daa=f.get_number("area_daa", errors),
            planting_year=f.get_number("planting_year", errors, integer=True),
            row_spacing_m=f.get_number("row_spacing_m", errors),
            plant_spacing_m=f.get_number("plant_spacing_m", errors),
            lat=f.get_number("lat", errors),
            lon=f.get_number("lon", errors),
            active=bool(f.get("active")),
        )
        if errors:
            raise ValidationError(errors)
        codes = self.app.ref.cultures.keys() if self.app.ref else None
        repo.save_field(self.app.conn, field, codes)
