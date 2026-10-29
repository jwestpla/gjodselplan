"""Gi tilbakemelding / Send tilbakemelding."""

from __future__ import annotations

import webbrowser
from tkinter import messagebox
from typing import TYPE_CHECKING

from .. import __version__, repo
from ..attachments import reveal_path
from ..config import FEEDBACK_EMAIL
from ..feedback import append_feedback, count_feedback, mailto_url
from ..validation import ValidationError
from .widgets import EditDialog

if TYPE_CHECKING:
    from .main_window import MainWindow

GENERAL = "Generelt"


class FeedbackDialog(EditDialog):
    def __init__(self, master, app: MainWindow, current_tab: str):
        super().__init__(master, "Gi tilbakemelding")
        self.app = app
        f = self.form
        f.entry("name", "Ditt navn *", width=30)
        f.combo("tab", "Gjelder fanen", [(t, t) for t in [*app.tabs, GENERAL]], width=28)
        f.entry("field", "Hvor? (felt, knapp, skifte …)", width=44)
        f.text("comment", "Kommentar *", height=7, width=50)
        f.set("name", app.reviewer_name(ask=False) or "")
        f.set("tab", current_tab if current_tab in app.tabs else GENERAL)
        self.form.widgets["comment"].focus_set()

    def save(self) -> None:
        f = self.form
        name, comment = f.get("name").strip(), f.get("comment").strip()
        errors = []
        if not name:
            errors.append("Skriv navnet ditt.")
        if not comment:
            errors.append("Skriv en kommentar.")
        if errors:
            raise ValidationError(errors)
        if name != (self.app.reviewer_name(ask=False) or ""):
            repo.save_settings(self.app.conn, {"reviewer_name": name})
        enterprise = repo.get_enterprise(self.app.conn, self.app.enterprise_id) if self.app.enterprise_id else None
        append_feedback(self.app.paths.feedback_file, {
            "versjon": __version__, "navn": name, "fane": f.get("tab"), "felt": f.get("field").strip(),
            "foretak": enterprise.name if enterprise else "", "år": str(self.app.year), "kommentar": comment,
        })
        count = count_feedback(self.app.paths.feedback_file)
        self.app.set_status(f"Takk! Tilbakemeldingen er lagret ({count} totalt). Bruk «Send tilbakemelding» når du er ferdig.")


def give_feedback(app: MainWindow) -> None:
    current = app.notebook.tab(app.notebook.select(), "text")
    FeedbackDialog(app, app, current).show()


def send_feedback(app: MainWindow) -> None:
    path = app.paths.feedback_file
    count = count_feedback(path)
    if count == 0:
        if messagebox.askyesno("Send tilbakemelding", "Du har ikke skrevet noen tilbakemeldinger ennå.\n\n"
                                                      "Vil du skrive en nå?", parent=app):
            give_feedback(app)
        return
    webbrowser.open(mailto_url(FEEDBACK_EMAIL, __version__, path, count))
    app.clipboard_clear()
    app.clipboard_append(FEEDBACK_EMAIL)
    reveal_path(path)
    messagebox.showinfo(
        "Send tilbakemelding",
        f"E-postprogrammet ditt skal nå åpne en ny e-post til {FEEDBACK_EMAIL}.\n\n"
        f"Legg ved filen feedback.csv. Mappen med filen er åpnet for deg.\n\n"
        f"Åpnet ikke e-posten seg (for eksempel hvis du bruker e-post i nettleseren)? "
        f"Send en e-post til {FEEDBACK_EMAIL} – adressen er kopiert – og legg ved filen:\n{path}",
        parent=app,
    )
