"""Entry point: python app.py   (packaged: Gjodselplan.exe)

    --selftest   check the installation without user interaction; writes selftest.log
                 in the data folder and exits with 0 (OK) or 1 (error)
    --version    print the version
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

if sys.version_info < (3, 11):
    sys.exit("Gjødselplan krever Python 3.11 eller nyere.")

try:
    import tkinter as tk
    from tkinter import messagebox
except ModuleNotFoundError:
    sys.exit(
        "Denne Python-installasjonen mangler tkinter. Bruk Python fra python.org, eller "
        f"installer Tk-støtte (Homebrew: brew install python-tk@{sys.version_info[0]}.{sys.version_info[1]})."
    )

from gjodsel import __version__, config, db
from gjodsel.reference import ReferenceDataError, load_reference


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        print(__version__)
        return 0
    if "--selftest" in args:
        return selftest()

    paths = config.get_paths()
    try:
        notes = config.prepare_data_dir(paths)
    except OSError as exc:
        _show_fatal(f"Kunne ikke opprette datamappen\n{paths.data_dir}\n\n{exc}")
        return 1
    _setup_logging(paths)
    log = logging.getLogger("gjodselplan")
    log.info("Start versjon %s (pakket=%s), data i %s", __version__, config.is_frozen(), paths.data_dir)
    try:
        conn = db.open_database(paths.db_file, paths.backups_dir, config.BACKUP_KEEP)
    except Exception as exc:
        log.exception("Kunne ikke åpne databasen")
        _show_fatal(f"Kunne ikke åpne databasen\n{paths.db_file}\n\n{exc}")
        return 1

    try:
        ref, ref_errors = load_reference(paths.reference_dir), []
    except ReferenceDataError as exc:
        ref, ref_errors = None, exc.errors
        log.warning("Feil i referansedata: %s", exc.errors)

    from gjodsel.ui.main_window import MainWindow

    window = MainWindow(conn, paths, ref, ref_errors, notes)
    try:
        window.mainloop()
    finally:
        conn.close()
    return 0


def selftest() -> int:
    """Headless check of a (packaged) installation: data folder, reference data, database,
    plan calculation, both PDFs and the main window with every tab."""
    paths = config.get_paths()
    lines = [f"Gjødselplan {__version__} selvtest", f"pakket={config.is_frozen()} python={sys.version.split()[0]}",
             f"program={sys.executable}", f"datamappe={paths.data_dir}"]
    code = 1
    try:
        notes = config.prepare_data_dir(paths)
        lines.append(f"første start: {notes or 'nei'}")
        ref = load_reference(paths.reference_dir)
        lines.append(f"referansedata: {len(ref.cultures)} kulturer, {len(ref.products)} produkter")
        conn = db.open_database(paths.db_file, paths.backups_dir, config.BACKUP_KEEP)
        from gjodsel import pdf_export, plan_service, repo
        from gjodsel.demo import DEMO_NAME

        settings = repo.load_settings(conn)
        enterprises = repo.list_enterprises(conn)
        lines.append(f"foretak: {[e.name for e in enterprises]}")
        if "demo" in notes and [e.name for e in enterprises] != [DEMO_NAME]:
            raise RuntimeError("demodatabasen inneholder noe annet enn Testgård")
        plans = [p for e in enterprises for p in repo.list_plans(conn, e.id)]
        if "demo" in notes and not plans:
            raise RuntimeError("demodatabasen mangler plan")
        if plans:
            view = plan_service.build_view(conn, ref, settings, plans[-1])
            plan_pdf = pdf_export.export_plan(view, paths.export_dir / "selvtest_plan.pdf", paths.data_dir)
            journal_pdf = pdf_export.export_journal(view, paths.export_dir / "selvtest_journal.pdf")
            lines.append(f"plan {view.year}: {len(view.rows)} skifter, {view.legal_text}, {view.p_summary}")
            lines.append(f"PDF: {plan_pdf.stat().st_size} + {journal_pdf.stat().st_size} byte")
        from gjodsel.ui.main_window import MainWindow

        window = MainWindow(conn, paths, ref, [])
        window.update()
        for tab in window.tabs.values():
            window.notebook.select(tab)
            window.update()
        lines.append(f"vindu: {window.title()} – {len(window.tabs)} faner vist")
        window.destroy()
        conn.close()
        lines.append("OK")
        code = 0
    except Exception:
        lines.append(traceback.format_exc())
        lines.append("FEIL")
    text = "\n".join(lines) + "\n"
    try:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        (paths.data_dir / "selftest.log").write_text(text, encoding="utf-8")
    except OSError:
        pass
    if sys.stdout is not None:
        print(text, end="")
    return code


def _setup_logging(paths: config.Paths) -> None:
    handler = RotatingFileHandler(paths.log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # A windowed exe has no console: give print/tracebacks somewhere harmless to go.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    sys.excepthook = lambda *exc: logging.getLogger("gjodselplan").critical("Ufanget feil", exc_info=exc)


def _show_fatal(message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Gjødselplan", message, parent=root)
    root.destroy()


if __name__ == "__main__":
    sys.exit(main())
