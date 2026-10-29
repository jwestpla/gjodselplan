"""Merge a reviewer's package (Gjennomgang_<navn>_<dato>.zip) into data/reference/.

    python tools/merge_review.py Gjennomgang_Kari_2026-11-02.zip            # show only
    python tools/merge_review.py Gjennomgang_Kari_2026-11-02.zip --apply    # ask per file
    python tools/merge_review.py PAKKE.zip --apply --yes                    # accept everything

Shows the manifest, a row-level diff per reference file, the reviewer's
assessments (Godkjent / Endret / Uenig + comments), the change log and the
feedback. With --apply you choose per file: all changes, one by one, or skip.
The result is validated with the app's own reference checks before anything
is written; on errors nothing is changed.
"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gjodsel.reference import ReferenceDataError, load_reference  # noqa: E402
from gjodsel.reference_review import (  # noqa: E402
    DELETED_ROW,
    NEW_ROW,
    Change,
    RawTable,
    diff_by_key,
    read_raw,
    row_key,
    write_raw,
)


def main(argv: list[str] | None = None, ask=input) -> int:
    parser = argparse.ArgumentParser(description="Flett en gjennomgangspakke inn i data/reference/.")
    parser.add_argument("package", type=Path)
    parser.add_argument("--reference-dir", type=Path, default=ROOT / "data" / "reference")
    parser.add_argument("--apply", action="store_true", help="spør per fil og skriv godkjente endringer")
    parser.add_argument("--yes", action="store_true", help="godta alle endringer uten å spørre (med --apply)")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        unpacked = Path(tmp) / "pakke"
        with zipfile.ZipFile(args.package) as z:
            z.extractall(unpacked)
        _print_text(unpacked / "manifest.txt", "Manifest")
        reviews = _read_csv(unpacked / "review_status.csv")

        accepted: dict[str, RawTable] = {}
        for incoming in sorted((unpacked / "reference").glob("*.csv")):
            current_path = args.reference_dir / incoming.name
            current = read_raw(current_path) if current_path.exists() else RawTable(incoming.name, [], [])
            theirs = read_raw(incoming)
            changes = diff_by_key(incoming.name, current, theirs)
            print(f"\n=== {incoming.name}: {len(changes)} endring(er) mot repoet ===")
            for c in changes:
                print("  " + _describe(c))
            _print_reviews(reviews, incoming.name)
            if not changes or not args.apply:
                continue
            choice = "a" if args.yes else ask(f"Bruk endringene i {incoming.name}? [a]lle / [e]n og en / [h]opp over: ")
            choice = (choice or "h").strip().lower()[:1]
            if choice == "a":
                accepted[incoming.name] = theirs
            elif choice == "e":
                chosen = [c for c in changes if (ask(f"  {_describe(c)}  – bruk? [j/N]: ") or "n").lower().startswith("j")]
                if chosen:
                    accepted[incoming.name] = apply_changes(current, theirs, chosen)

        other = sorted({r["file"] for r in reviews} - {p.name for p in (unpacked / "reference").glob("*.csv")})
        for file in other:
            print(f"\n=== {file}: vurdert, ikke endret ===")
            _print_reviews(reviews, file)
        _print_csv(unpacked / "reference_changes.csv", "Endringslogg fra appen")
        _print_csv(unpacked / "feedback.csv", "Tilbakemeldinger")

        if not args.apply:
            print("\nIngen filer endret (kjør med --apply for å flette).")
            return 0
        if not accepted:
            print("\nIngen endringer valgt.")
            return 0
        return _write_validated(args.reference_dir, accepted)


def apply_changes(current: RawTable, theirs: RawTable, chosen: list[Change]) -> RawTable:
    """Apply selected row-level changes from the reviewer's table to the current one."""
    file = current.file
    rows = {row_key(file, r): dict(r) for r in current.rows}
    order = [row_key(file, r) for r in current.rows]
    theirs_rows = {row_key(file, r): r for r in theirs.rows}
    for c in chosen:
        if c.column == NEW_ROW:
            rows[c.row_key] = dict(theirs_rows[c.row_key])
            order.append(c.row_key)
        elif c.column == DELETED_ROW:
            rows.pop(c.row_key, None)
            order = [k for k in order if k != c.row_key]
        else:
            rows[c.row_key][c.column] = c.new
    header = current.header or theirs.header
    return RawTable(file, header, [rows[k] for k in order if k in rows])


def _write_validated(reference_dir: Path, accepted: dict[str, RawTable]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp)
        for f in reference_dir.glob("*.csv"):
            shutil.copy2(f, staged)
        for name, table in accepted.items():
            write_raw(staged / name, table)
        try:
            load_reference(staged)
        except ReferenceDataError as exc:
            print("\nFEIL – endringene gir ugyldige referansedata, ingenting er skrevet:")
            for e in exc.errors:
                print(f"  • {e}")
            return 1
        for name in accepted:
            shutil.copy2(staged / name, reference_dir / name)
            print(f"Skrev {reference_dir / name}")
    print("Ferdig. Kjør testene og se over endringene med git diff før du committer.")
    return 0


def _describe(c: Change) -> str:
    if c.column == NEW_ROW:
        return f"+ {c.row_key}: {c.new}"
    if c.column == DELETED_ROW:
        return f"- {c.row_key}: {c.old}"
    return f"~ {c.row_key}: {c.column} «{c.old}» → «{c.new}»"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")), delimiter=";"))


def _print_reviews(reviews: list[dict[str, str]], file: str) -> None:
    for r in (r for r in reviews if r["file"] == file):
        comment = f" – {r['comment']}" if r.get("comment") else ""
        print(f"    [{r['status']}] {r['row_key']}{comment} ({r.get('reviewer', '')})")


def _print_csv(path: Path, title: str) -> None:
    rows = _read_csv(path)
    print(f"\n=== {title}: {len(rows)} rad(er) ===")
    for r in rows:
        print("  " + " | ".join(v for v in r.values() if v))


def _print_text(path: Path, title: str) -> None:
    if path.exists():
        print(f"=== {title} ===\n{path.read_text(encoding='utf-8').strip()}")


if __name__ == "__main__":
    sys.exit(main())
