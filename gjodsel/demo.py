"""Anonymised demo data ("Testgård") for the packaged build and for reviewers.

Everything here is invented: kommunenr 9999 does not exist, there is no org.nr,
phone or e-mail, and coordinates are rounded.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import replace
from datetime import date
from pathlib import Path

from . import db, plan_service, repo
from .attachments import store_attachment
from .models import Application, Enterprise, Field, Property, SoilSample
from .reference import load_reference
from .soil import P_AL_UNIT_P2O5, p_al_to_element

DEMO_NAME = "Testgård"

# (field_no, name, culture, daa, years since planting, variety, map rectangle x0, y0, x1, y1)
_FIELDS = (
    ("1", "Eplehagen", "eple", 12.0, 11, "Aroma", (40, 40, 420, 300)),
    ("2", "Nyplanting eple", "eple", 4.0, 0, "Discovery", (40, 320, 420, 460)),
    ("3", "Plommer", "plomme", 6.0, 8, "Reeves", (440, 40, 760, 230)),
    ("4", "Morell", "sotkirsebaer", 8.0, 7, "Lapins", (440, 250, 760, 460)),
)


def build_demo(out_dir: Path, reference_dir: Path, year: int | None = None) -> Path:
    """Create out_dir/gjodselplan.db (+ attachments/) with one enterprise and a draft plan for `year`."""
    year = year or date.today().year
    out_dir.mkdir(parents=True, exist_ok=True)
    db_file = out_dir / "gjodselplan.db"
    if db_file.exists():
        db_file.unlink()
    conn = db.connect(db_file)
    db.migrate(conn)
    ref = load_reference(reference_dir)
    settings = repo.load_settings(conn)

    e = repo.save_enterprise(conn, Enterprise(
        name=DEMO_NAME, contact_first_name="Demo", contact_last_name="Bruker", place="Testbygd",
        notes="Anonymisert demoforetak. Ingen ekte data.",
    ))
    prop = repo.save_property(conn, Property(enterprise_id=e.id, farm_name=DEMO_NAME, kommunenr="9999", gnr=1, bnr=1))
    fields = {}
    for no, name, culture, area, age, variety, _rect in _FIELDS:
        fields[no] = repo.save_field(conn, Field(
            property_id=prop.id, field_no=no, name=name, area_daa=area, culture_code=culture,
            planting_year=year - age, variety=variety, row_spacing_m=4.0 if culture == "sotkirsebaer" else 3.5,
            plant_spacing_m=1.5 if culture != "eple" else 1.0, lat=61.1, lon=7.5,
            cover="tunnel" if culture == "sotkirsebaer" else "ingen",
        ))

    map_file = out_dir / "demokart.png"
    map_file.write_bytes(_map_png())
    repo.set_property_map(conn, prop.id, store_attachment(out_dir, e.id, map_file))
    map_file.unlink()

    repo.save_soil_sample(conn, SoilSample(
        enterprise_id=e.id, sample_date=f"{year - 1}-04-15", label="Prøve A", lab="Testlab", p_al=9.0, k_al=12.0,
        mg_al=11.0, ca_al=120.0, ph=6.2, mold_pct=5.0, lat=61.1, lon=7.5,
    ), [fields["1"].id, fields["2"].id])
    repo.save_soil_sample(conn, SoilSample(
        enterprise_id=e.id, sample_date=f"{year - 2}-05-02", label="Prøve B (høy P-AL)", lab="Testlab",
        p_al=p_al_to_element(57, P_AL_UNIT_P2O5), k_al=18.0, mg_al=9.0, ph=5.2, lat=61.1, lon=7.5,
        notes="P-AL registrert som 57 mg P₂O₅/100 g, omregnet til 24,9 mg P/100 g.",
    ), [fields["3"].id])
    repo.save_soil_sample(conn, SoilSample(
        enterprise_id=e.id, sample_date=f"{year - 10}-04-20", label="Prøve C (utløpt)", lab="Testlab",
        p_al=7.0, k_al=20.0, ph=6.0, lat=61.1, lon=7.5,
    ), [fields["4"].id])

    plan = plan_service.create_plan(conn, ref, settings, e.id, year)
    seasons = {s.field_id: s for s in repo.list_field_seasons(conn, plan.id)}
    for no, growth in (("1", "20to40"), ("3", "gt40"), ("4", "lt20")):
        plan_service.save_season(conn, ref, settings, replace(seasons[fields[no].id], growth_class=growth))
    plan_service.apply_template(conn, ref, [seasons[fields["1"].id].id], "Eple bærende standard")
    plan_service.apply_template(conn, ref, [seasons[fields["2"].id].id], "Nyplanting")
    plan_service.apply_template(conn, ref, [seasons[fields["3"].id].id], "Plomme bærende")
    for product, kg, timing in (("fg_12_4_18", 40, "tidlig_vaar"), ("kalksalpeter", 10, "etter_hosting")):
        plan_service.save_application(conn, ref, Application(
            field_season_id=seasons[fields["4"].id].id, product_code=product, kg_per_daa=kg, timing_code=timing))
    spread = date(year, 4, 20)
    if spread <= date.today():  # the journal refuses dates in the future
        plan_service.save_application(conn, ref, Application(
            field_season_id=seasons[fields["1"].id].id, kind="utfort", product_code="opti_ns_27", kg_per_daa=10,
            date=spread.isoformat(), performed_by="Demo Bruker"))
    conn.close()
    return db_file


def enterprise_names(db_file: Path) -> list[str]:
    conn = db.connect(db_file)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM enterprise ORDER BY name")]
    finally:
        conn.close()


def _map_png(width: int = 800, height: int = 500) -> bytes:
    """A plain sketch of the four demo fields. Pillow is used for labels when available."""
    colours = ((46, 125, 50), (124, 179, 66), (123, 31, 162), (198, 40, 40))
    try:
        from io import BytesIO

        from PIL import Image, ImageDraw

        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        for (no, name, *_rest, rect), colour in zip(_FIELDS, colours):
            draw.rectangle(rect, outline=colour, width=5)
            draw.text((rect[0] + 12, rect[1] + 10), f"Skifte {no} {name}", fill=(0, 0, 0))
        draw.text((10, height - 20), "Demokart – Testgård (oppdiktet)", fill=(90, 90, 90))
        buf = BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except ImportError:
        pixels = [[(255, 255, 255)] * width for _ in range(height)]
        for (*_info, rect), colour in zip(_FIELDS, colours):
            x0, y0, x1, y1 = rect
            for t in range(5):
                for x in range(x0, x1):
                    pixels[y0 + t][x] = pixels[y1 - t][x] = colour
                for y in range(y0, y1):
                    pixels[y][x0 + t] = pixels[y][x1 - t] = colour
        raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
