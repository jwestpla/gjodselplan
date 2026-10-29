# PyInstaller build for Gjødselplan (one-folder: dist/Gjodselplan/Gjodselplan.exe).
#
#   python tools/make_demo_db.py
#   pyinstaller --noconfirm --clean gjodselplan.spec
#   python tools/check_bundle.py
#
# One-folder rather than one-file: starts faster, no unpacking to %TEMP% on every
# start, and fewer antivirus false alarms. Only data/reference/*.csv and the
# generated demo data (build/demo) are bundled – never data/gjodselplan.db.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)
DEMO = ROOT / "build" / "demo"
if not (DEMO / "gjodselplan.db").exists():
    raise SystemExit("Demodata mangler. Kjør først: python tools/make_demo_db.py")

datas = [(str(p), "reference") for p in sorted((ROOT / "data" / "reference").glob("*.csv"))]
datas.append((str(DEMO / "gjodselplan.db"), "demo"))
for f in sorted((DEMO / "attachments").rglob("*")):
    if f.is_file():
        datas.append((str(f), str(Path("demo") / f.relative_to(DEMO).parent)))

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    # reportlab loads the standard-font width tables by name at run time
    hiddenimports=collect_submodules("reportlab.pdfbase"),
    excludes=["pytest", "_pytest", "PyInstaller", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Gjodselplan",
    console=sys.platform != "win32",  # windowed on Windows; console elsewhere so --selftest prints
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Gjodselplan", upx=False)
