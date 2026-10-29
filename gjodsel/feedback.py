"""Reviewer feedback: appended to feedback.csv in the data folder, sent by e-mail (mailto)."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

COLUMNS = ("tidspunkt", "versjon", "navn", "fane", "felt", "foretak", "år", "kommentar")


def append_feedback(path: Path, entry: dict[str, str]) -> None:
    """Append one row; the file gets a header (and BOM, for Excel) when it is created."""
    new = not path.exists() or path.stat().st_size == 0
    row = {"tidspunkt": datetime.now().isoformat(timespec="seconds"), **entry}
    with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=";", extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") or "" for k in COLUMNS})


def count_feedback(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh, delimiter=";"))


def mailto_url(email: str, version: str, feedback_path: Path, count: int) -> str:
    subject = f"Tilbakemelding Gjødselplan {version}"
    body = (
        f"Hei!\n\nHer er tilbakemeldingen min på Gjødselplan (versjon {version}).\n\n"
        f"Jeg har skrevet {count} tilbakemelding(er). De ligger i denne filen:\n{feedback_path}\n\n"
        "Husk å legge ved filen – eller gjennomgangspakken fra fanen Referansedata – før du sender.\n"
    )
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"
