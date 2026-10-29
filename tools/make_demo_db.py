"""Build the anonymised demo database that ships with the Windows build.

    python tools/make_demo_db.py [--year 2026] [--out build/demo]

Writes build/demo/gjodselplan.db and build/demo/attachments/. Only invented
data ("Testgård") goes in; the script refuses to finish if anything else does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gjodsel.config import REFERENCE_DIR  # noqa: E402
from gjodsel.demo import DEMO_NAME, build_demo, enterprise_names  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, default=None, help="planår for demoplanen (standard: i år)")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "demo")
    args = parser.parse_args()
    if args.out.exists():
        import shutil
        shutil.rmtree(args.out)
    db_file = build_demo(args.out, REFERENCE_DIR, args.year)
    names = enterprise_names(db_file)
    if names != [DEMO_NAME]:
        print(f"FEIL: demobasen inneholder andre foretak enn {DEMO_NAME}: {names}", file=sys.stderr)
        return 1
    print(f"Demodata skrevet til {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
