"""External review of the reference CSVs (Referansedata tab and tools/merge_review.py).

Edits are checked with reference.load_reference on a temporary copy before
anything is written, so a save can never leave gaps or overlaps in the bands.
The review state (review_status.csv) and the change log (reference_changes.csv)
live in separate files in the data folder; the reference CSVs stay clean.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .reference import ReferenceDataError, is_unverified, load_reference, table_columns
from .units import format_plain, parse_number

REVIEW_STATUSES = ("Ikke vurdert", "Godkjent", "Endret", "Uenig")
REVIEWED = ("Godkjent", "Endret", "Uenig")
STATUS_COLUMNS = ("file", "row_key", "status", "comment", "reviewer", "timestamp")
CHANGE_COLUMNS = ("file", "row_key", "column", "old", "new", "reviewer", "timestamp")
NEW_ROW = "(ny rad)"
DELETED_ROW = "(slettet rad)"


@dataclass(frozen=True)
class TableInfo:
    file: str
    title: str
    description: str


# Display order and Norwegian explanations for the reviewer.
TABLE_INFO: tuple[TableInfo, ...] = (
    TableInfo("cultures.csv", "Kulturer og grunnormer",
              "Grunnorm for N, P og K per kultur, avlingsgrunnlag og ekstra N per tonn avling. "
              "Startpunktet for hver behovsberegning."),
    TableInfo("age_factors.csv", "Aldersfaktorer og stadium",
              "Stadium (planteår, ungtre, bærende) ut fra år siden planting, og faktorene N, P og K ganges med. "
              "Avgjør også om avlingsjustering brukes."),
    TableInfo("n_growth_adjustment.csv", "N-justering etter nyvekst",
              "kg N/daa som legges til eller trekkes fra etter skuddtilvekst i fjor. Brukes i N-beregningen."),
    TableInfo("n_mold_correction.csv", "N-korreksjon etter moldinnhold",
              "kg N/daa etter moldinnhold i jordprøven. Brukes bare når innstillingen for moldkorreksjon er på."),
    TableInfo("p_al_correction.csv", "P-korreksjon etter P-AL",
              "Prosent opp eller ned på P-normen ut fra P-AL i jordprøven (mg P/100 g). Brukes i P-beregningen."),
    TableInfo("k_correction.csv", "K-korreksjon etter K-AL og K-HNO₃",
              "Prosent opp eller ned på K-normen ut fra K-AL og K-HNO₃. Raden merket use_when_hno3_missing = 1 "
              "brukes når jordprøven mangler K-HNO₃."),
    TableInfo("soil_optimum.csv", "Optimale jordverdier",
              "Optimumsområde for pH, P-AL, K-AL, Mg-AL og Ca-AL. Gir merknader i planen, men endrer ikke tallene."),
    TableInfo("p_limits.csv", "P-grenser (tak)",
              "Største tillatte kg P/daa per år og region, alle gjødselslag samlet. Brukes i P-sjekken for foretaket."),
    TableInfo("products.csv", "Gjødselprodukter",
              "Næringsinnhold i element-% og pris. Brukes til å regne om planlagt mengde til N, P og K, "
              "og i innkjøpslisten."),
    TableInfo("templates.csv", "Gjødslingsmaler",
              "Ferdige oppsett for planlagt gjødsling som kan brukes med «Bruk mal…» i planen."),
    TableInfo("timings.csv", "Tidspunkter",
              "Tidspunkter for gjødsling, og om de hører til våren eller sommer/høst (innkjøpslisten)."),
)


def info_for(file: str) -> TableInfo:
    return next(i for i in TABLE_INFO if i.file == file)


# --- Raw CSV access -------------------------------------------------------------------------------


@dataclass
class RawTable:
    file: str
    header: list[str]
    rows: list[dict[str, str]]


def read_raw(path: Path) -> RawTable:
    """The file as strings, exactly as written (decimal comma kept)."""
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")), delimiter=";")
    header = [(h or "").strip().lstrip("﻿") for h in reader.fieldnames or []]
    reader.fieldnames = header
    rows = []
    for raw in reader:
        if not any((v or "").strip() for k, v in raw.items() if k is not None):
            continue
        rows.append({h: (raw.get(h) or "").strip() for h in header})
    return RawTable(path.name, header, rows)


def write_raw(path: Path, table: RawTable) -> None:
    """UTF-8 with BOM (Excel), ';' separator; written to a temp file first, then swapped in."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\n")
        writer.writerow(table.header)
        for row in table.rows:
            writer.writerow([row.get(h, "") for h in table.header])
    os.replace(tmp, path)


def _n(text: str) -> str:
    try:
        value = parse_number(text)
    except ValueError:
        return text.strip()
    return "" if value is None else format_plain(value)


_KEYS = {
    "cultures.csv": lambda r: r["code"],
    "products.csv": lambda r: r["code"],
    "timings.csv": lambda r: r["code"],
    "n_growth_adjustment.csv": lambda r: r["growth_class"],
    "age_factors.csv": lambda r: r["stage"],
    "soil_optimum.csv": lambda r: r["parameter"],
    "p_limits.csv": lambda r: f"{r['region']} {_n(r['year_from'])}–{_n(r['year_to'])}",
    "templates.csv": lambda r: f"{r['template']} #{_n(r['order'])}",
    "p_al_correction.csv": lambda r: f"P-AL {_n(r['pal_min'])}–{_n(r['pal_max'])}",
    "n_mold_correction.csv": lambda r: f"Mold {_n(r['mold_min'])}–{_n(r['mold_max'])}",
    "k_correction.csv": lambda r: (f"K-HNO₃ {_n(r['khno3_min'])}–{_n(r['khno3_max'])} / "
                                   f"K-AL {_n(r['kal_min'])}–{_n(r['kal_max'])}"),
}


def row_key(file: str, row: dict[str, str]) -> str:
    """Readable, stable identifier of a row (code, stage, band …) used in review status and change log."""
    try:
        return _KEYS[file](row)
    except KeyError:
        return ";".join(row.values())


def needs_review(row: dict[str, str]) -> bool:
    return "source" in row and is_unverified(row["source"])


def check_cell(file: str, column: str, value: str) -> str | None:
    """Type check for one cell; returns a Norwegian error or None. Ranges/bands are checked on save."""
    spec = {c.name: c for c in table_columns(file)}.get(column)
    text = value.strip()
    if spec is None:
        return None
    if not text:
        return f"«{column}» kan ikke være tom." if spec.required else None
    if spec.kind == "str":
        return None
    try:
        number = parse_number(text)
    except ValueError:
        return f"«{text}» er ikke et tall. Bruk komma eller punktum som desimaltegn."
    if spec.kind == "int" and not number.is_integer():
        return f"«{column}» må være et heltall."
    if spec.kind == "flag" and number not in (0, 1):
        return f"«{column}» må være 0 eller 1."
    return None


def validate_table(ref_dir: Path, table: RawTable) -> list[str]:
    """Run the full reference validation with this table replacing its file. [] = OK."""
    with tempfile.TemporaryDirectory() as tmp:
        for f in ref_dir.glob("*.csv"):
            shutil.copy2(f, tmp)
        write_raw(Path(tmp) / table.file, table)
        try:
            load_reference(Path(tmp))
        except ReferenceDataError as exc:
            return exc.errors
    return []


# --- Editing ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    file: str
    row_key: str
    column: str
    old: str
    new: str


@dataclass
class Row:
    id: int
    values: dict[str, str]


@dataclass
class EditSession:
    """In-memory edits of one file. Rows keep their id through edits so the diff is reliable
    even when key columns (band limits, codes) change."""

    file: str
    header: list[str]
    original: dict[int, dict[str, str]]
    rows: list[Row]
    next_id: int

    @classmethod
    def load(cls, ref_dir: Path, file: str) -> EditSession:
        table = read_raw(ref_dir / file)
        rows = [Row(i, dict(r)) for i, r in enumerate(table.rows)]
        return cls(file, table.header, {r.id: dict(r.values) for r in rows}, rows, len(rows))

    def table(self) -> RawTable:
        return RawTable(self.file, self.header, [dict(r.values) for r in self.rows])

    def row(self, row_id: int) -> Row:
        return next(r for r in self.rows if r.id == row_id)

    def set(self, row_id: int, column: str, value: str) -> None:
        self.row(row_id).values[column] = value.strip()

    def add_row(self, copy_of: int | None = None) -> Row:
        values = dict(self.row(copy_of).values) if copy_of is not None else {h: "" for h in self.header}
        row = Row(self.next_id, values)
        self.next_id += 1
        index = next((i for i, r in enumerate(self.rows) if r.id == copy_of), len(self.rows) - 1)
        self.rows.insert(index + 1, row)
        return row

    def delete_row(self, row_id: int) -> None:
        self.rows = [r for r in self.rows if r.id != row_id]

    def is_dirty(self, row_id: int) -> bool:
        return self.original.get(row_id) != self.row(row_id).values

    @property
    def dirty(self) -> bool:
        return any(self.is_dirty(r.id) for r in self.rows) or len(self.rows) != len(self.original)

    def changes(self) -> tuple[list[Change], dict[str, str]]:
        """(changes, {old_key: new_key}) compared with the file as loaded."""
        changes: list[Change] = []
        renames: dict[str, str] = {}
        present = set()
        for r in self.rows:
            old = self.original.get(r.id)
            new_key = row_key(self.file, r.values)
            if old is None:
                changes.append(Change(self.file, new_key, NEW_ROW, "", _joined(self.header, r.values)))
                continue
            present.add(r.id)
            old_key = row_key(self.file, old)
            for column in self.header:
                if old.get(column, "") != r.values.get(column, ""):
                    changes.append(Change(self.file, old_key, column, old.get(column, ""), r.values.get(column, "")))
            if new_key != old_key:
                renames[old_key] = new_key
        for row_id, old in self.original.items():
            if row_id not in present:
                changes.append(Change(self.file, row_key(self.file, old), DELETED_ROW, _joined(self.header, old), ""))
        return changes, renames


def _joined(header: list[str], values: dict[str, str]) -> str:
    return ";".join(values.get(h, "") for h in header)


def save_session(ref_dir: Path, session: EditSession, reviewer: str, changes_path: Path,
                 status_path: Path) -> list[Change]:
    """Validate, write the file, log the changes and update review status. Raises ReferenceDataError."""
    errors = validate_table(ref_dir, session.table())
    if errors:
        raise ReferenceDataError(errors)
    changes, renames = session.changes()
    if not changes:
        return []
    write_raw(ref_dir / session.file, session.table())
    append_changes(changes_path, changes, reviewer)
    rename_review_keys(status_path, session.file, renames)
    entries = load_review_status(status_path)
    for key in {renames.get(c.row_key, c.row_key) for c in changes if c.column != DELETED_ROW}:
        current = entries.get((session.file, key))
        comment = current.comment if current else ""
        if current is None or current.status != "Endret":
            set_review(status_path, session.file, key, "Endret", comment, reviewer)
    for c in changes:
        if c.column == DELETED_ROW:
            set_review(status_path, session.file, c.row_key, "Ikke vurdert", "", reviewer)
    return changes


def diff_by_key(file: str, old: RawTable, new: RawTable) -> list[Change]:
    """Row-key based diff between two versions of a file (for merging a review package)."""
    old_rows = {row_key(file, r): r for r in old.rows}
    new_rows = {row_key(file, r): r for r in new.rows}
    header = new.header or old.header
    changes = []
    for key, row in new_rows.items():
        if key not in old_rows:
            changes.append(Change(file, key, NEW_ROW, "", _joined(header, row)))
            continue
        for column in header:
            if old_rows[key].get(column, "") != row.get(column, ""):
                changes.append(Change(file, key, column, old_rows[key].get(column, ""), row.get(column, "")))
    for key, row in old_rows.items():
        if key not in new_rows:
            changes.append(Change(file, key, DELETED_ROW, _joined(old.header, row), ""))
    return changes


# --- Review status and change log ----------------------------------------------------------------


@dataclass(frozen=True)
class ReviewEntry:
    status: str
    comment: str
    reviewer: str
    timestamp: str


def _read_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh, delimiter=";")]


def _write_dicts(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def load_review_status(path: Path) -> dict[tuple[str, str], ReviewEntry]:
    return {(r["file"], r["row_key"]): ReviewEntry(r["status"], r.get("comment") or "", r.get("reviewer") or "",
                                                     r.get("timestamp") or "")
            for r in _read_dicts(path)}


def set_review(path: Path, file: str, key: str, status: str, comment: str, reviewer: str) -> None:
    """Upsert one row's review. 'Ikke vurdert' without comment removes the entry."""
    if status not in REVIEW_STATUSES:
        raise ValueError(status)
    rows = [r for r in _read_dicts(path) if (r["file"], r["row_key"]) != (file, key)]
    if status != "Ikke vurdert" or comment.strip():
        rows.append({"file": file, "row_key": key, "status": status, "comment": comment.strip(),
                     "reviewer": reviewer, "timestamp": datetime.now().isoformat(timespec="seconds")})
    _write_dicts(path, STATUS_COLUMNS, rows)


def rename_review_keys(path: Path, file: str, renames: dict[str, str]) -> None:
    if not renames or not path.exists():
        return
    rows = _read_dicts(path)
    for r in rows:
        if r["file"] == file and r["row_key"] in renames:
            r["row_key"] = renames[r["row_key"]]
    _write_dicts(path, STATUS_COLUMNS, rows)


def append_changes(path: Path, changes: list[Change], reviewer: str) -> None:
    new = not path.exists() or path.stat().st_size == 0
    stamp = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CHANGE_COLUMNS, delimiter=";")
        if new:
            writer.writeheader()
        for c in changes:
            writer.writerow({"file": c.file, "row_key": c.row_key, "column": c.column, "old": c.old, "new": c.new,
                             "reviewer": reviewer, "timestamp": stamp})


def read_changes(path: Path) -> list[dict[str, str]]:
    return _read_dicts(path)


def review_counts(ref_dir: Path, status_path: Path) -> dict[str, tuple[int, int]]:
    """file -> (reviewed, to_review). Rows to review: unverified source, or already given a review."""
    entries = load_review_status(status_path)
    counts = {}
    for info in TABLE_INFO:
        path = ref_dir / info.file
        if not path.exists():
            continue
        reviewed = total = 0
        for row in read_raw(path).rows:
            entry = entries.get((info.file, row_key(info.file, row)))
            if needs_review(row) or entry is not None:
                total += 1
                reviewed += entry is not None and entry.status in REVIEWED
        counts[info.file] = (reviewed, total)
    return counts


# --- Review package ---------------------------------------------------------------------------------


def changed_reference_files(ref_dir: Path, bundled_dir: Path, changes_path: Path) -> list[Path]:
    """Reference files that differ from the shipped version, plus any file in the change log."""
    names = {r["file"] for r in read_changes(changes_path)}
    if ref_dir.resolve() != bundled_dir.resolve():
        for f in ref_dir.glob("*.csv"):
            original = bundled_dir / f.name
            if not original.exists() or _content(f) != _content(original):
                names.add(f.name)
    return [ref_dir / n for n in sorted(names) if (ref_dir / n).exists()]


def _content(path: Path) -> tuple:
    table = read_raw(path)
    return tuple(table.header), tuple(tuple(r.get(h, "") for h in table.header) for r in table.rows)


def safe_name(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text.strip(), flags=re.UNICODE).strip("_") or "ukjent"


def make_review_package(ref_dir: Path, bundled_dir: Path, data_dir: Path, reviewer: str, version: str,
                        out_dir: Path) -> Path:
    """Gjennomgang_<navn>_<dato>.zip with changed reference CSVs, review status, change log and feedback."""
    status_path = data_dir / "review_status.csv"
    changes_path = data_dir / "reference_changes.csv"
    feedback_path = data_dir / "feedback.csv"
    stem = f"Gjennomgang_{safe_name(reviewer)}_{date.today().isoformat()}"
    target = out_dir / f"{stem}.zip"
    n = 2
    while target.exists():
        target = out_dir / f"{stem}_{n}.zip"
        n += 1
    changed = changed_reference_files(ref_dir, bundled_dir, changes_path)
    counts = review_counts(ref_dir, status_path)
    reviewed = sum(c[0] for c in counts.values())
    total = sum(c[1] for c in counts.values())
    manifest = [
        f"Gjødselplan versjon {version}",
        f"Vurdert av: {reviewer}",
        f"Laget: {datetime.now().isoformat(timespec='minutes')}",
        f"Vurdert: {reviewed} av {total} verdier",
        "Endrede referansefiler: " + (", ".join(f.name for f in changed) or "ingen"),
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in changed:
            z.write(f, f"reference/{f.name}")
        for f in (status_path, changes_path, feedback_path):
            if f.exists():
                z.write(f, f.name)
        z.writestr("manifest.txt", "\n".join(manifest) + "\n")
    return target
