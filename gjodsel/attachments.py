"""Copy attached files (soil reports, map sketches) into the data folder.

The database stores paths relative to the data directory, e.g.
'attachments/3/kart.pdf', so the data folder can be moved as a whole.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def store_attachment(data_dir: Path, enterprise_id: int, source: Path) -> str:
    """Copy `source` into attachments/<enterprise_id>/ and return the relative path."""
    target_dir = data_dir / "attachments" / str(enterprise_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-. ]", "_", source.stem).strip() or "vedlegg"
    target = target_dir / f"{stem}{source.suffix.lower()}"
    n = 1
    while target.exists():
        target = target_dir / f"{stem}_{n}{source.suffix.lower()}"
        n += 1
    shutil.copy2(source, target)
    return target.relative_to(data_dir).as_posix()


def resolve(data_dir: Path, relative_path: str) -> Path:
    return data_dir / relative_path


def open_path(path: Path) -> None:
    """Open a file or folder with the operating system's default program."""
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def reveal_path(path: Path) -> None:
    """Show a file selected in Explorer / Finder."""
    if sys.platform == "win32":
        subprocess.run(["explorer", f"/select,{path}"], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    else:
        open_path(path.parent)


def documents_dir() -> Path:
    """The user's Documents folder (follows OneDrive redirection on Windows)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # CSIDL_PERSONAL
                return Path(buf.value)
        except (OSError, AttributeError):
            pass
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()
