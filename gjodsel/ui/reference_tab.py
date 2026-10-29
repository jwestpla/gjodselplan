"""Referansedata: review and edit the reference CSVs (for an external reviewer)."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import __version__, plan_service
from ..attachments import documents_dir, open_path, reveal_path
from ..config import FEEDBACK_EMAIL, bundled_reference_dir
from ..reference import ReferenceDataError, load_reference
from ..reference_review import (
    REVIEW_STATUSES,
    REVIEWED,
    TABLE_INFO,
    EditSession,
    check_cell,
    load_review_status,
    make_review_package,
    needs_review,
    review_counts,
    row_key,
    save_session,
    set_review,
    validate_table,
)
from .widgets import RED, button_row, scrolled_tree, show_errors

if TYPE_CHECKING:
    from .main_window import MainWindow

_BG = {"needs": "#fff3b0", "approved": "#e3f5e1", "disagree": "#fde2e1"}
_DIRTY_FG = "#1f5fbf"


class ReferenceTab(ttk.Frame):
    def __init__(self, master, app: MainWindow):
        super().__init__(master, padding=8)
        self.app = app
        self.session: EditSession | None = None
        self.editor: ttk.Entry | None = None
        self.columns: dict[str, str] = {}  # tree column id -> CSV column
        self.file_var = tk.StringVar()
        self.only_review = tk.BooleanVar(value=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Tabell").pack(side="left")
        self.file_combo = ttk.Combobox(top, textvariable=self.file_var, state="readonly", width=36,
                                       values=[i.title for i in TABLE_INFO])
        self.file_combo.pack(side="left", padx=6)
        self.file_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_file_selected())
        ttk.Checkbutton(top, text="Vis bare verdier som må vurderes", variable=self.only_review,
                        command=self._render).pack(side="left", padx=12)
        ttk.Button(top, text="Endre navn", command=self._change_name).pack(side="right")
        self.reviewer_label = ttk.Label(top, text="")
        self.reviewer_label.pack(side="right", padx=6)

        self.description = ttk.Label(self, text="", wraplength=900, justify="left")
        self.description.grid(row=1, column=0, sticky="w", pady=(6, 2))
        self.counter = ttk.Label(self, text="", font=("TkDefaultFont", 0, "bold"))
        self.counter.grid(row=2, column=0, sticky="w", pady=(0, 4))

        frame, self.tree = scrolled_tree(self, [], height=14, xscroll=True)
        frame.grid(row=3, column=0, sticky="nsew")
        for tag, colour in _BG.items():
            self.tree.tag_configure(tag, background=colour)
        self.tree.tag_configure("dirty", foreground=_DIRTY_FG)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_review())

        self.problem_label = tk.Label(self, text="", fg=RED, justify="left", wraplength=900, anchor="w")
        self.problem_label.grid(row=4, column=0, sticky="ew", pady=(4, 0))

        bottom = ttk.Frame(self)
        bottom.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        button_row(bottom, [
            ("Ny rad", self.add_row),
            ("Slett rad", self.delete_row),
            ("Lagre endringer", self.save),
            ("Angre endringer", self.revert),
        ]).pack(side="left")
        ttk.Button(bottom, text="Åpne mappen i Utforsker", command=lambda: open_path(self.app.paths.reference_dir)
                   ).pack(side="right")
        ttk.Button(bottom, text="Lag gjennomgangspakke", command=self.make_package).pack(side="right", padx=6)

        review = ttk.LabelFrame(self, text="Vurdering av valgt rad", padding=6)
        review.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        self.review_key = ttk.Label(review, text="Velg en rad i tabellen.", width=40)
        self.review_key.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.status_var = tk.StringVar(value=REVIEW_STATUSES[0])
        ttk.Combobox(review, textvariable=self.status_var, values=REVIEW_STATUSES, state="readonly",
                     width=14).grid(row=0, column=1)
        ttk.Label(review, text="Kommentar").grid(row=0, column=2, padx=(12, 4))
        self.comment_var = tk.StringVar()
        ttk.Entry(review, textvariable=self.comment_var, width=60).grid(row=0, column=3, sticky="ew")
        ttk.Button(review, text="Lagre vurdering", command=self.save_review).grid(row=0, column=4, padx=(8, 0))
        review.columnconfigure(3, weight=1)
        ttk.Label(self, text="Dobbeltklikk en celle for å endre. Gul = må vurderes (kilde ANTATT / SJEKK ETIKETT), "
                             "grønn = vurdert, rød = uenig, blå tekst = ikke lagret.", foreground="#666666"
                  ).grid(row=7, column=0, sticky="w", pady=(4, 0))
        self.file_var.set(TABLE_INFO[0].title)

    # --- Loading and rendering ------------------------------------------------------------

    @property
    def file(self) -> str:
        return next(i.file for i in TABLE_INFO if i.title == self.file_var.get())

    def refresh(self) -> None:
        if self.session is None or self.session.file != self.file or not self.session.dirty:
            self._load()
        self._render()

    def _load(self) -> None:
        self._close_editor()
        try:
            self.session = EditSession.load(self.app.paths.reference_dir, self.file)
        except (OSError, KeyError) as exc:
            self.session = None
            self.problem_label.configure(text=f"Kan ikke lese {self.file}: {exc}")
            return
        self.problem_label.configure(text="")

    def _on_file_selected(self) -> None:
        if self.session is not None and self.session.dirty and self.session.file != self.file:
            answer = messagebox.askyesnocancel(
                "Ulagrede endringer", f"Du har endringer i {self.session.file} som ikke er lagret.\n\n"
                                      "Ja = lagre, Nei = forkast, Avbryt = bli i tabellen.", parent=self)
            if answer is None:
                self.file_var.set(next(i.title for i in TABLE_INFO if i.file == self.session.file))
                return
            if answer and not self._save_session():
                self.file_var.set(next(i.title for i in TABLE_INFO if i.file == self.session.file))
                return
        self._load()
        self._render()

    def _render(self) -> None:
        self._close_editor()
        info = next(i for i in TABLE_INFO if i.file == self.file)
        self.description.configure(text=f"{info.file}: {info.description}")
        self.reviewer_label.configure(text=f"Vurdert av: {self.app.reviewer_name(ask=False) or '(ikke satt)'}")
        self.tree.delete(*self.tree.get_children())
        if self.session is None:
            return
        header = self.session.header
        self.columns = {f"c{i}": name for i, name in enumerate(header)}
        tree_columns = [*self.columns, "_status", "_comment"]
        self.tree["columns"] = tree_columns
        for col_id, name in self.columns.items():
            self.tree.heading(col_id, text=name, anchor="w")
            self.tree.column(col_id, width=max(60, min(len(name) * 9, 140)) if name != "source" else 260,
                             minwidth=40, stretch=True, anchor="w")
        for col_id, title, width in (("_status", "Vurdering", 95), ("_comment", "Kommentar", 220)):
            self.tree.heading(col_id, text=title, anchor="w")
            self.tree.column(col_id, width=width, minwidth=60, stretch=True, anchor="w")

        entries = load_review_status(self.app.paths.review_status_file)
        for row in self.session.rows:
            key = self._key(row.id)
            entry = entries.get((self.file, key))
            needs = needs_review(row.values) or entry is not None
            if self.only_review.get() and not needs:
                continue
            tags = []
            if entry is not None and entry.status == "Uenig":
                tags.append("disagree")
            elif entry is not None and entry.status in REVIEWED:
                tags.append("approved")
            elif needs:
                tags.append("needs")
            if self.session.is_dirty(row.id):
                tags.append("dirty")
            self.tree.insert("", "end", iid=str(row.id), tags=tags, values=[
                *(row.values.get(h, "") for h in header),
                entry.status if entry else ("Ikke vurdert" if needs else ""), entry.comment if entry else "",
            ])
        self._update_counter()
        self._show_review()

    def _update_counter(self) -> None:
        counts = review_counts(self.app.paths.reference_dir, self.app.paths.review_status_file)
        done, total = counts.get(self.file, (0, 0))
        all_done = sum(c[0] for c in counts.values())
        all_total = sum(c[1] for c in counts.values())
        unsaved = "   ·   ULAGREDE ENDRINGER" if self.session and self.session.dirty else ""
        self.counter.configure(text=f"Denne tabellen: {done} av {total} verdier vurdert   ·   "
                                    f"Alle tabeller: {all_done} av {all_total} vurdert{unsaved}")

    def _key(self, row_id: int) -> str:
        """Review key: from the saved values, so unsaved edits of key columns don't lose the review."""
        values = self.session.original.get(row_id) or self.session.row(row_id).values
        return row_key(self.file, values)

    def _selected_row_id(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    # --- Cell editing ---------------------------------------------------------------------

    def _on_double_click(self, event) -> None:
        if self.session is None or self.tree.identify_region(event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        col_id = self.tree["columns"][int(col[1:]) - 1]
        if col_id not in self.columns:  # review columns are edited in the panel below
            self.tree.selection_set(item)
            return
        x, y, w, h = self.tree.bbox(item, col)
        self._close_editor()
        row_id, column = int(item), self.columns[col_id]
        editor = ttk.Entry(self.tree)
        editor.insert(0, self.session.row(row_id).values.get(column, ""))
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=max(w, 120 if column != "source" else 300), height=h)
        editor.focus_set()
        editor.bind("<Return>", lambda _e: self._commit(row_id, column, keep_open_on_error=True))
        editor.bind("<KP_Enter>", lambda _e: self._commit(row_id, column, keep_open_on_error=True))
        editor.bind("<Escape>", lambda _e: self._close_editor())
        editor.bind("<FocusOut>", lambda _e: self._commit(row_id, column, keep_open_on_error=False))
        self.editor = editor

    def _commit(self, row_id: int, column: str, keep_open_on_error: bool) -> None:
        if self.editor is None:
            return
        value = self.editor.get()
        error = check_cell(self.file, column, value)
        if error:
            self.problem_label.configure(text=f"Ikke endret: {error}")
            if keep_open_on_error:
                self.bell()
                return
            self._close_editor()
            return
        self._close_editor()
        if self.session.row(row_id).values.get(column, "") != value.strip():
            self.session.set(row_id, column, value)
            self._check_pending()
        self._render()
        if self.tree.exists(str(row_id)):
            self.tree.selection_set(str(row_id))

    def _close_editor(self) -> None:
        if self.editor is not None:
            editor, self.editor = self.editor, None
            editor.destroy()

    def _check_pending(self) -> None:
        """Show (but don't block on) problems in the unsaved table, e.g. a gap while editing bands."""
        errors = validate_table(self.app.paths.reference_dir, self.session.table()) if self.session.dirty else []
        self.problem_label.configure(
            text=("Kan ikke lagres ennå: " + " · ".join(errors[:4]) + (" …" if len(errors) > 4 else "")) if errors else "")

    # --- Rows, saving ---------------------------------------------------------------------------

    def add_row(self) -> None:
        if self.session is None:
            return
        row = self.session.add_row(copy_of=self._selected_row_id())
        self._check_pending()
        self._render()
        self.tree.selection_set(str(row.id))
        self.tree.see(str(row.id))

    def delete_row(self) -> None:
        row_id = self._selected_row_id()
        if row_id is None or self.session is None:
            return
        if messagebox.askyesno("Slett rad", f"Slette raden «{self._key(row_id)}»?\n(Slettingen lagres først når du "
                                            "trykker «Lagre endringer».)", parent=self):
            self.session.delete_row(row_id)
            self._check_pending()
            self._render()

    def revert(self) -> None:
        if self.session is not None and self.session.dirty and messagebox.askyesno(
            "Angre", "Forkaste alle endringer som ikke er lagret?", parent=self
        ):
            self._load()
            self._render()

    def save(self) -> None:
        if self.session is None or not self.session.dirty:
            messagebox.showinfo("Lagre", "Ingen endringer å lagre.", parent=self)
            return
        self._save_session()

    def _save_session(self) -> bool:
        reviewer = self.app.reviewer_name(ask=True)
        if not reviewer:
            return False
        paths = self.app.paths
        try:
            changes = save_session(paths.reference_dir, self.session, reviewer, paths.reference_changes_file,
                                   paths.review_status_file)
        except ReferenceDataError as exc:
            show_errors(self, ["Endringene er ikke lagret:"] + exc.errors, "Kan ikke lagre")
            return False
        file = self.session.file
        self._load()
        effects = []
        try:
            ref = load_reference(paths.reference_dir)
        except ReferenceDataError as exc:  # can't happen after validation, but never leave the app half-updated
            show_errors(self, exc.errors, "Feil i referansedata")
            return False
        settings = self.app.settings()
        if settings is not None:
            effects = plan_service.recalculate_drafts(self.app.conn, ref, settings)
        self.app.set_reference(ref)
        text = f"{len(changes)} endring(er) i {file} er lagret og logget.\n\n"
        if effects:
            shown = effects[:20]
            text += "Behovet i åpne planer er beregnet på nytt:\n" + "\n".join(f"• {e}" for e in shown)
            if len(effects) > len(shown):
                text += f"\n… og {len(effects) - len(shown)} til."
        else:
            text += "Behovet i de åpne planene ble ikke endret."
        text += "\n\nFerdigstilte planer endres ikke."
        messagebox.showinfo("Lagret", text, parent=self)
        return True

    # --- Review ---------------------------------------------------------------------------------

    def _show_review(self) -> None:
        row_id = self._selected_row_id()
        if row_id is None or self.session is None:
            self.review_key.configure(text="Velg en rad i tabellen.")
            self.status_var.set(REVIEW_STATUSES[0])
            self.comment_var.set("")
            return
        key = self._key(row_id)
        entry = load_review_status(self.app.paths.review_status_file).get((self.file, key))
        self.review_key.configure(text=f"Rad: {key}")
        self.status_var.set(entry.status if entry else REVIEW_STATUSES[0])
        self.comment_var.set(entry.comment if entry else "")

    def save_review(self) -> None:
        row_id = self._selected_row_id()
        if row_id is None or self.session is None:
            return
        if row_id not in self.session.original:
            messagebox.showinfo("Vurdering", "Lagre den nye raden før du vurderer den.", parent=self)
            return
        reviewer = self.app.reviewer_name(ask=True)
        if not reviewer:
            return
        if self.status_var.get() == "Uenig" and not self.comment_var.get().strip():
            show_errors(self, ["Skriv en kommentar når du er uenig, så vi vet hva som bør endres."])
            return
        set_review(self.app.paths.review_status_file, self.file, self._key(row_id), self.status_var.get(),
                   self.comment_var.get(), reviewer)
        self._render()
        self.tree.selection_set(str(row_id))
        self.app.set_status(f"Vurdering lagret for «{self._key(row_id)}».")

    def _change_name(self) -> None:
        self.app.change_reviewer_name()
        self._render()

    def make_package(self) -> None:
        if self.session is not None and self.session.dirty:
            if not messagebox.askyesno("Ulagrede endringer", "Du har endringer som ikke er lagret. Lagre dem først?",
                                       parent=self) or not self._save_session():
                return
        reviewer = self.app.reviewer_name(ask=True)
        if not reviewer:
            return
        paths = self.app.paths
        try:
            target = make_review_package(paths.reference_dir, bundled_reference_dir(), paths.data_dir, reviewer,
                                         __version__, documents_dir())
        except OSError as exc:
            show_errors(self, [f"Kunne ikke lage pakken: {exc}"], "Gjennomgangspakke")
            return
        reveal_path(target)
        messagebox.showinfo(
            "Gjennomgangspakke",
            f"Pakken er laget:\n{target}\n\nSend den som vedlegg på e-post til {FEEDBACK_EMAIL}. "
            "Mappen er åpnet for deg.", parent=self)
