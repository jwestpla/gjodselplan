"""Innstillinger: advisor info, calculation settings, reference data reload and product editor."""

from __future__ import annotations

from dataclasses import fields, replace
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import repo
from ..attachments import open_path
from ..reference import Product, ReferenceDataError, save_products
from ..units import format_number, format_plain, parse_number
from ..validation import ValidationError
from .widgets import AMBER, EditDialog, Form, button_row, scrolled_tree, show_errors

if TYPE_CHECKING:
    from .main_window import MainWindow

_ADVISOR_KEYS = (("advisor_name", "Navn"), ("advisor_company", "Firma"), ("advisor_phone", "Telefon"),
                 ("advisor_email", "E-post"))


class SettingsTab(ttk.Frame):
    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=8)
        self.app = app
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        advisor = ttk.LabelFrame(self, text="Rådgiver (vises på PDF-en)", padding=8)
        advisor.grid(row=0, column=0, sticky="nw")
        self.advisor_form = Form(advisor)
        self.advisor_form.pack(anchor="w")
        for key, label in _ADVISOR_KEYS:
            self.advisor_form.entry(key, label)

        calc = ttk.LabelFrame(self, text="Beregning og regelverk", padding=8)
        calc.grid(row=0, column=1, sticky="nw", padx=(8, 0))
        f = self.calc_form = Form(calc)
        f.pack(anchor="w")
        f.check("apply_mold_correction", "Bruk N-korreksjon for moldinnhold")
        f.entry("p_skew_factor", "Maks skjevfordeling P per skifte (faktor)", width=8)
        f.entry("plan_threshold_daa", "Plangrense (daa gjødslet areal)", width=8)
        f.entry("soil_max_age_years", "Maks alder jordprøve (år)", width=8)
        ttk.Label(calc, text="Endringer påvirker utkast. Ferdigstilte planer endres ikke.",
                  foreground="#666666").pack(anchor="w", pady=(4, 0))
        ttk.Button(self, text="Lagre innstillinger", command=self.save).grid(row=0, column=2, sticky="nw", padx=8)

        products = ttk.LabelFrame(self, text="Produkter (products.csv) – verdier i element-%", padding=8)
        products.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        products.columnconfigure(0, weight=1)
        products.rowconfigure(0, weight=1)
        frame, self.product_tree = scrolled_tree(products, [
            ("code", "Kode", 120), ("name", "Navn", 200), ("n", "N", 45), ("p", "P", 45), ("k", "K", 45),
            ("mg", "Mg", 45), ("s", "S", 45), ("ca", "Ca", 45), ("b", "B", 45), ("price", "kr/kg", 55),
            ("source", "Kilde", 180),
        ], height=8, numeric=("n", "p", "k", "mg", "s", "ca", "b", "price"))
        frame.grid(row=0, column=0, sticky="nsew")
        self.product_tree.tag_configure("unverified", foreground=AMBER)
        self.product_tree.bind("<Double-1>", lambda _e: self.edit_product())
        button_row(products, [
            ("Nytt produkt", self.new_product),
            ("Rediger", self.edit_product),
            ("Slett", self.delete_product),
        ]).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(products, text="Oransje = kilde «SJEKK ETIKETT»/«ANTATT». Sett kilden til f.eks. «Etikett kontrollert "
                                 "2026» når verdiene er sjekket.", foreground="#666666").grid(row=2, column=0, sticky="w")

        ref_box = ttk.LabelFrame(self, text="Referansedata", padding=8)
        ref_box.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.ref_label = ttk.Label(ref_box, text="")
        self.ref_label.pack(side="left")
        ttk.Button(ref_box, text="Åpne mappen", command=lambda: open_path(self.app.paths.reference_dir)).pack(
            side="right")
        ttk.Button(ref_box, text="Last referansedata på nytt", command=self.app.reload_reference).pack(
            side="right", padx=6)

    def refresh(self) -> None:
        settings = repo.get_settings(self.app.conn)
        for key, _ in _ADVISOR_KEYS:
            self.advisor_form.set(key, settings.get(key))
        self.calc_form.set("apply_mold_correction", settings.get("apply_mold_correction") == "1")
        for key in ("p_skew_factor", "plan_threshold_daa", "soil_max_age_years"):
            try:
                self.calc_form.set(key, format_plain(parse_number(settings.get(key))))
            except ValueError:
                self.calc_form.set(key, settings.get(key))
        self.product_tree.delete(*self.product_tree.get_children())
        if self.app.ref is not None:
            for p in self.app.ref.products.values():
                self.product_tree.insert("", "end", iid=p.code, tags=("unverified",) if p.unverified else (), values=(
                    p.code, p.name, *(format_plain(getattr(p, n)) for n in ("n", "p", "k", "mg", "s", "ca", "b")),
                    format_number(p.price_nok_per_kg, 2), p.source,
                ))
        unverified = len(self.app.ref.unverified_rows()) if self.app.ref else 0
        state = f"lastet, {unverified} ikke-verifiserte rader" if self.app.ref else "FEIL – ikke lastet"
        self.ref_label.configure(text=f"{self.app.paths.reference_dir} · {state}")

    def save(self) -> None:
        f = self.calc_form
        errors: list[str] = []
        skew = f.get_number("p_skew_factor", errors)
        threshold = f.get_number("plan_threshold_daa", errors)
        max_age = f.get_number("soil_max_age_years", errors, integer=True)
        if skew is None or skew < 1:
            errors.append("Maks skjevfordeling må være 1 eller større (1,10 = +10 %).")
        if threshold is None or threshold < 0:
            errors.append("Plangrensen må være 0 eller større.")
        if max_age is None or max_age < 1:
            errors.append("Maks alder for jordprøve må være minst 1 år.")
        if errors:
            show_errors(self, errors)
            return
        values = {key: self.advisor_form.get(key).strip() or None for key, _ in _ADVISOR_KEYS}
        values.update(apply_mold_correction="1" if f.get("apply_mold_correction") else "0",
                      p_skew_factor=repr(skew), plan_threshold_daa=repr(threshold), soil_max_age_years=str(max_age))
        repo.save_settings(self.app.conn, values)
        self.app.data_changed()
        self.app.set_status("Innstillinger lagret.")

    # --- Products ---------------------------------------------------------------------

    def _products(self) -> list[Product]:
        return list(self.app.ref.products.values()) if self.app.ref else []

    def _write(self, products: list[Product]) -> bool:
        try:
            ref = save_products(self.app.paths.reference_dir, products)
        except ReferenceDataError as exc:
            show_errors(self, ["products.csv ble ikke endret:"] + exc.errors, "Kan ikke lagre")
            return False
        self.app.set_reference(ref)
        return True

    def new_product(self) -> None:
        if self.app.ref is None:
            return
        dialog = ProductDialog(self, None, set(self.app.ref.products))
        if dialog.show():
            self._write(self._products() + [dialog.result])

    def edit_product(self) -> None:
        selection = self.product_tree.selection()
        if not selection or self.app.ref is None:
            return
        product = self.app.ref.products[selection[0]]
        dialog = ProductDialog(self, product, set(self.app.ref.products) - {product.code})
        if dialog.show():
            self._write([dialog.result if p.code == product.code else p for p in self._products()])

    def delete_product(self) -> None:
        selection = self.product_tree.selection()
        if not selection or self.app.ref is None:
            return
        code = selection[0]
        used = repo.product_in_use(self.app.conn, code)
        if used:
            show_errors(self, [f"Produktet brukes i {used} planlagte/utførte gjødslinger og kan ikke slettes."],
                        "Kan ikke slette")
            return
        if messagebox.askyesno("Slett produkt", f"Slette «{code}» fra products.csv?", icon="warning", parent=self):
            self._write([p for p in self._products() if p.code != code])


class ProductDialog(EditDialog):
    _NUMBERS = (("n", "N %", True), ("p", "P % (element)", True), ("k", "K % (element)", True), ("mg", "Mg %", False),
                ("s", "S %", False), ("ca", "Ca %", False), ("b", "B %", False),
                ("price_nok_per_kg", "Pris kr/kg", False))

    def __init__(self, master, product: Product | None, taken_codes: set[str]):
        super().__init__(master, "Nytt produkt" if product is None else f"Rediger {product.code}")
        self.product = product
        self.taken = taken_codes
        self.result: Product | None = None
        f = self.form
        f.entry("code", "Kode * (uten mellomrom)", width=24)
        f.entry("name", "Navn *", width=34)
        for key, label, required in self._NUMBERS:
            f.entry(key, label + (" *" if required else ""), width=10)
        f.entry("source", "Kilde *", width=34)
        ttk.Label(self.extra, text="Oppgi P og K som rent element (P = P₂O₅ × 0,4364, K = K₂O × 0,8301). "
                                   "Tomt felt = ukjent.", foreground="#666666").pack(anchor="w")
        if product is not None:
            for fl in fields(product):
                f.set(fl.name, getattr(product, fl.name))
            f.widgets["code"].state(["disabled"])  # code is referenced by plans and templates
        else:
            f.set("source", "SJEKK ETIKETT")

    def save(self) -> None:
        f = self.form
        errors: list[str] = []
        code = (f.get("code") or "").strip()
        if not code or " " in code or ";" in code:
            errors.append("Koden må fylles ut, uten mellomrom eller semikolon.")
        elif code in self.taken:
            errors.append(f"Koden «{code}» finnes allerede.")
        if not f.get("name").strip():
            errors.append("Navn må fylles ut.")
        if not f.get("source").strip():
            errors.append("Kilde må fylles ut (bruk «SJEKK ETIKETT» hvis verdiene ikke er kontrollert).")
        values = {}
        for key, label, required in self._NUMBERS:
            value = f.get_number(key, errors)
            if value is None and required:
                errors.append(f"{label} må fylles ut.")
            elif value is not None and key != "price_nok_per_kg" and not 0 <= value <= 100:
                errors.append(f"{label} må være 0–100.")
            values[key] = value
        if errors:
            raise ValidationError(errors)
        base = self.product or Product(code="", name="", n=0, p=0, k=0, mg=None, s=None, ca=None, b=None,
                                       price_nok_per_kg=None, source="")
        self.result = replace(base, code=code, name=f.get("name").strip(), source=f.get("source").strip(), **values)
