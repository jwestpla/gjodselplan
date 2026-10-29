"""File locations.

Development (python app.py): client data in the project's data/ folder,
reference CSVs in data/reference/ (versioned in git).

Packaged (PyInstaller, sys.frozen): everything the user can change lives in a
per-user folder – %APPDATA%\\Gjodselplan on Windows – never next to the exe.
That includes the reference CSVs, which are copied there from the bundle on
first start (only files that are missing, so a reviewer's edits survive).

GJODSEL_DATA overrides the data folder in both modes.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Gjodselplan"
FEEDBACK_EMAIL = "joepwestplate@gmail.com"
PROJECT_DIR = Path(__file__).resolve().parent.parent
REFERENCE_DIR = PROJECT_DIR / "data" / "reference"
BACKUP_KEEP = 20


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    reference_dir: Path
    db_file: Path
    attachments_dir: Path
    backups_dir: Path
    export_dir: Path

    @property
    def feedback_file(self) -> Path:
        return self.data_dir / "feedback.csv"

    @property
    def review_status_file(self) -> Path:
        return self.data_dir / "review_status.csv"

    @property
    def reference_changes_file(self) -> Path:
        return self.data_dir / "reference_changes.csv"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "gjodselplan.log"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Read-only resources shipped with the program (PyInstaller's _MEIPASS when packaged)."""
    return Path(getattr(sys, "_MEIPASS", PROJECT_DIR))


def bundled_reference_dir() -> Path:
    """The reference CSVs as shipped (for first-start copy and for spotting a reviewer's changes)."""
    return bundle_dir() / "reference" if is_frozen() else REFERENCE_DIR


def bundled_demo_dir() -> Path:
    """Demo database + attachments made by tools/make_demo_db.py (only shipped in the packaged build)."""
    return bundle_dir() / "demo" if is_frozen() else PROJECT_DIR / "build" / "demo"


def default_data_dir() -> Path:
    if not is_frozen():
        return PROJECT_DIR / "data"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def get_paths() -> Paths:
    env = os.environ.get("GJODSEL_DATA")
    data_dir = Path(env).expanduser().resolve() if env else default_data_dir()
    return Paths(
        data_dir=data_dir,
        reference_dir=data_dir / "reference" if is_frozen() else REFERENCE_DIR,
        db_file=data_dir / "gjodselplan.db",
        attachments_dir=data_dir / "attachments",
        backups_dir=data_dir / "backups",
        export_dir=data_dir / "export",
    )


def ensure_dirs(paths: Paths) -> None:
    for d in (paths.data_dir, paths.attachments_dir, paths.backups_dir, paths.export_dir, paths.reference_dir):
        d.mkdir(parents=True, exist_ok=True)


def prepare_data_dir(paths: Paths) -> list[str]:
    """Create folders and, when packaged, copy what the user needs on first start.

    Returns short Norwegian notes about what was done (empty when nothing).
    """
    ensure_dirs(paths)
    notes: list[str] = []
    source = bundled_reference_dir()
    if paths.reference_dir.resolve() != source.resolve():
        copied = []
        for src in sorted(source.glob("*.csv")):
            dst = paths.reference_dir / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                copied.append(src.name)
        if copied:
            notes.append(f"Referansedata kopiert til {paths.reference_dir} ({len(copied)} filer).")

    demo = bundled_demo_dir()
    if is_frozen() and not paths.db_file.exists() and (demo / "gjodselplan.db").exists():
        shutil.copy2(demo / "gjodselplan.db", paths.db_file)
        if (demo / "attachments").is_dir():
            shutil.copytree(demo / "attachments", paths.attachments_dir, dirs_exist_ok=True)
        notes.append("demo")
    return notes
