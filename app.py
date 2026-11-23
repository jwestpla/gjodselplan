"""Entry point: python app.py   (packaged: Gjodselplan.exe)

    --selftest   check the installation without user interaction; writes selftest.log
                 in the data folder and exits with 0 (OK) or 1 (error)
    --karttest   check that the map services (Kartverket, NIBIO) can be reached over HTTPS;
                 writes karttest.log in the data folder, exit code 0 (OK) or 1
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
    if "--karttest" in args:
        return map_network_test()

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
            view = plan_service.build_view(conn, ref, settings, plans[-1], data_dir=paths.data_dir)
            plan_pdf = pdf_export.export_plan(view, paths.export_dir / "selvtest_plan.pdf", paths.data_dir)
            journal_pdf = pdf_export.export_journal(view, paths.export_dir / "selvtest_journal.pdf")
            lines.append(f"plan {view.year}: {len(view.rows)} skifter, {view.legal_text}, {view.p_summary}")
            lines.append(f"PDF: {plan_pdf.stat().st_size} + {journal_pdf.stat().st_size} byte")
            # Generated farm map without network: Pillow/JPEG and the map drawing must be in the bundle
            from dataclasses import asdict

            from gjodsel import farm_map

            fm, points = farm_map.selftest_map(paths.data_dir, paths.export_dir)
            first = view.properties[0] if view.properties else {"label": "Selvtest", "tenure": "eid"}
            view.properties = [{**first, "map_attachment": None, "generated_map": {
                **fm.to_dict(), "points": [asdict(p) for p in points], "missing_coordinates": []}}]
            map_pdf = pdf_export.export_plan(view, paths.export_dir / "selvtest_kart.pdf", paths.data_dir)
            lines.append(f"eiendomskart (uten nett): {map_pdf.stat().st_size} byte")
            from gjodsel import gardskart

            georef, error = gardskart.selftest(paths.export_dir)
            lines.append(f"flyfoto fra gårdskart (uten nett): treff {georef.score:.0%}, avvik {error:.2f} m")
            if not georef.confident or error > 1.0:
                raise RuntimeError("plasseringen av flyfoto fra gårdskart virker ikke")
        from gjodsel.ui.main_window import MainWindow

        window = MainWindow(conn, paths, ref, [])
        window.update()
        for tab in window.tabs.values():
            window.notebook.select(tab)
            window.update()
        properties = [p for e in enterprises for p in repo.list_properties(conn, e.id)]
        if properties:
            from gjodsel.ui.map_dialog import MapDialog

            MapDialog(window, window, properties[0]).update()  # shows the cached map or "not fetched"
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


def map_network_test() -> int:
    """Reach the open map services over HTTPS (needs internet; not part of --selftest)."""
    from gjodsel import farm_map

    paths = config.get_paths()
    lines = [f"Gjødselplan {__version__} karttest", *farm_map.network_check()]
    code = 0 if all(line.startswith("OK") for line in lines[1:]) else 1
    text = "\n".join(lines + ["OK" if code == 0 else "FEIL"]) + "\n"
    try:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        (paths.data_dir / "karttest.log").write_text(text, encoding="utf-8")
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
