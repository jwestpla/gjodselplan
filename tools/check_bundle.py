"""Check a PyInstaller build before it is shipped: demo data only, reference CSVs present.

    python tools/check_bundle.py [dist/Gjodselplan]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gjodsel.demo import DEMO_NAME, enterprise_names  # noqa: E402
from gjodsel.reference import REFERENCE_FILES  # noqa: E402

FORBIDDEN = {"feedback.csv", "review_status.csv", "reference_changes.csv", "gjodselplan.log", "selftest.log"}


def check(dist: Path) -> list[str]:
    problems = []
    databases = [p for p in dist.rglob("*") if p.suffix.lower() in {".db", ".sqlite", ".sqlite3"}]
    demo_dbs = [p for p in databases if p.parent.name == "demo" and p.name == "gjodselplan.db"]
    for p in databases:
        if p not in demo_dbs:
            problems.append(f"uventet database i bygget: {p}")
    if len(demo_dbs) != 1:
        problems.append(f"fant {len(demo_dbs)} demodatabaser, ventet 1")
    for p in demo_dbs:
        names = enterprise_names(p)
        if names != [DEMO_NAME]:
            problems.append(f"{p} inneholder andre foretak enn {DEMO_NAME}: {names}")
    for p in dist.rglob("*"):
        if p.name in FORBIDDEN or "backups" in p.parts:
            problems.append(f"klientfil i bygget: {p}")
    reference = next((d for d in dist.rglob("reference") if d.is_dir()), None)
    present = {p.name for p in reference.glob("*.csv")} if reference else set()
    for name in REFERENCE_FILES:
        if name not in present:
            problems.append(f"mangler referansefil {name}")
    return problems


def main() -> int:
    dist = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "Gjodselplan"
    problems = check(dist)
    for p in problems:
        print(f"FEIL: {p}")
    if not problems:
        print(f"OK: {dist} inneholder bare demodata ({DEMO_NAME}) og alle referansefiler.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
