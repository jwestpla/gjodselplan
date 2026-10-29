"""Foretak: list of all enterprises and a form for the selected one."""

from __future__ import annotations

from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import repo
from ..models import Enterprise
from ..reference import DEFAULT_REGION
from ..validation import ValidationError
from .widgets import Form, button_row, scrolled_tree, selected_id, show_errors

if TYPE_CHECKING:
    from .main_window import MainWindow

_FORM_KEYS = (
    "name", "org_nr", "contact_first_name", "contact_last_name", "street",
    "postcode", "place", "phone", "email", "region", "notes",
)
_UNLOADED = object()


class EnterpriseTab(ttk.Frame):
    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=8)
        self.app = app
        self.editing_id: int | None = None
        self._loaded_id = _UNLOADED
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        list_box = ttk.LabelFrame(self, text="Alle foretak", padding=6)
        list_box.grid(row=0, column=0, sticky="nsew")
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)
        frame, self.tree = scrolled_tree(list_box, [
            ("name", "Foretaksnavn", 220), ("org_nr", "Org.nr.", 100), ("contact", "Kontaktperson", 160),
            ("place", "Poststed", 120), ("phone", "Telefon", 90), ("region", "Region", 90),
        ], height=8)
        frame.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        form_box = ttk.LabelFrame(self, text="Foretaksopplysninger", padding=8)
        form_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.mode_label = ttk.Label(form_box, text="")
        self.mode_label.pack(anchor="w", pady=(0, 6))
        f = self.form = Form(form_box, columns=2)
        f.pack(anchor="w")
        f.entry("name", "Foretaksnavn *", width=34)
        f.entry("org_nr", "Org.nr.", width=14)
        f.entry("contact_first_name", "Kontaktperson, fornavn")
        f.entry("contact_last_name", "Kontaktperson, etternavn")
        f.text("notes", "Notater", height=3, width=34)
        f.entry("street", "Adresse", column=1)
        f.entry("postcode", "Postnr.", width=8, column=1)
        f.entry("place", "Poststed", column=1)
        f.entry("phone", "Telefon", width=16, column=1)
        f.entry("email", "E-post", column=1)
        f.combo("region", "Region (P-grense)", [], width=16, column=1)
        button_row(form_box, [
            ("Nytt foretak", self.new),
            ("Lagre", self.save),
            ("Slett foretak", self.delete),
        ]).pack(anchor="w", pady=(8, 0))

    # --- Data -------------------------------------------------------------------

    def _regions(self) -> list[str] | None:
        if self.app.ref is None:
            return None
        return sorted({limit.region for limit in self.app.ref.p_limits})

    def refresh(self) -> None:
        self.form.set_choices("region", [(r, r) for r in self._regions() or [DEFAULT_REGION]])
        self.tree.delete(*self.tree.get_children())
        for e in repo.list_enterprises(self.app.conn):
            self.tree.insert("", "end", iid=str(e.id), values=(
                e.name, e.org_nr or "", e.contact_name, e.place or "", e.phone or "", e.region,
            ))
        if self.app.enterprise_id is not None and self.tree.exists(str(self.app.enterprise_id)):
            self.tree.selection_set(str(self.app.enterprise_id))
            self.tree.see(str(self.app.enterprise_id))
        if self.app.enterprise_id != self._loaded_id:
            self._load(self.app.enterprise_id)

    def _load(self, enterprise_id: int | None) -> None:
        self._loaded_id = enterprise_id
        e = repo.get_enterprise(self.app.conn, enterprise_id) if enterprise_id is not None else None
        if e is None:
            self.new()
            return
        self.editing_id = e.id
        for key in _FORM_KEYS:
            self.form.set(key, getattr(e, key))
        self.mode_label.configure(text=f"Registrert {e.created_at}, sist endret {e.updated_at}")

    def _on_tree_select(self, _event=None) -> None:
        enterprise_id = selected_id(self.tree)
        if enterprise_id is not None and enterprise_id != self.app.enterprise_id:
            self.app.select_enterprise(enterprise_id)

    # --- Actions ----------------------------------------------------------------

    def new(self) -> None:
        self.editing_id = None
        for key in _FORM_KEYS:
            self.form.set(key, None)
        self.form.set("region", DEFAULT_REGION)
        self.mode_label.configure(text="Nytt foretak – fyll ut og trykk Lagre.")
        self.form.widgets["name"].focus_set()

    def save(self) -> None:
        e = Enterprise(id=self.editing_id, **{key: self.form.get(key) for key in _FORM_KEYS})
        try:
            saved = repo.save_enterprise(self.app.conn, e, self._regions())
        except ValidationError as exc:
            show_errors(self, exc.errors)
            return
        self._loaded_id = _UNLOADED  # reload the stored (cleaned) values
        self.app.select_enterprise(saved.id)
        self.app.set_status(f"Lagret {saved.name}.")

    def delete(self) -> None:
        if self.editing_id is None:
            return
        e = repo.get_enterprise(self.app.conn, self.editing_id)
        counts = repo.enterprise_dependents(self.app.conn, e.id)
        if counts["plans"]:
            show_errors(self, ["Foretaket har gjødslingsplaner og kan ikke slettes. "
                               "Planer og journaler skal oppbevares i 5 år."], "Kan ikke slette")
            return
        message = f"Slette «{e.name}»?"
        if counts["properties"] or counts["soil_samples"]:
            message += (
                f"\n\nDette sletter også {counts['properties']} eiendom(mer), "
                f"{counts['fields']} skifte(r) og {counts['soil_samples']} jordprøve(r)."
            )
        if not messagebox.askyesno("Slett foretak", message, icon="warning", parent=self):
            return
        try:
            repo.delete_enterprise(self.app.conn, e.id)
        except ValidationError as exc:
            show_errors(self, exc.errors, "Kan ikke slette")
            return
        self.app.select_enterprise(None)
        self.app.set_status(f"Slettet {e.name}.")
