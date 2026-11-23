from pathlib import Path

import pytest

from gjodsel import db, repo
from gjodsel.config import REFERENCE_DIR
from gjodsel.models import Enterprise, Field, Property
from gjodsel.reference import load_reference


FIXTURE_REFERENCE_DIR = Path(__file__).parent / "fixtures" / "reference"


@pytest.fixture(scope="session")
def ref():
    """The bundled (editable) reference data."""
    return load_reference(REFERENCE_DIR)


@pytest.fixture(scope="session")
def fixed_ref():
    """Frozen PLAN §4 reference values for calculation tests."""
    return load_reference(FIXTURE_REFERENCE_DIR)


@pytest.fixture
def settings(conn):
    """Default settings as seeded by migration 1."""
    return repo.load_settings(conn)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def farm(conn):
    """Anonymised test enterprise with one property and one apple field."""
    e = repo.save_enterprise(conn, Enterprise(name="Testgård", org_nr="923609016", place="Lærdal"))
    p = repo.save_property(conn, Property(enterprise_id=e.id, farm_name="Øvre Testgard",
                                          kommunenr="4642", gnr=12, bnr=3))
    f = repo.save_field(conn, Field(property_id=p.id, field_no="1", name="Eplehagen",
                                    area_daa=10.8, culture_code="eple", planting_year=2017))
    return e, p, f


# --- Generated farm maps: a fake network with made-up data (no real property or map in the repo) ---

# A made-up farm near 60.40 N, 6.70 E: one main teig (about 500 x 300 m), a small teig nearby and a
# woodland teig about 6 km away. The soil sample and field points below lie inside the main teig.
FAKE_TEIGER = [
    ([[[6.695, 60.398], [6.704, 60.398], [6.704, 60.4007], [6.695, 60.4007], [6.695, 60.398]]], True, "Grønt"),
    ([[[6.7045, 60.3982], [6.7055, 60.3982], [6.7055, 60.3988], [6.7045, 60.3982]]], False, "Gult"),
    ([[[6.80, 60.42], [6.81, 60.42], [6.81, 60.425], [6.80, 60.42]]], False, "Rødt"),
]


class FakeMapNetwork:
    """Answers the Eiendom API and the WMS services with generated data, and records the calls."""

    def __init__(self, teiger=None, ar5_ok=True, topo_ok=True, offline=False):
        self.teiger = FAKE_TEIGER if teiger is None else teiger
        self.ar5_ok, self.topo_ok, self.offline = ar5_ok, topo_ok, offline
        self.calls: list[str] = []

    def __call__(self, url: str):
        import io
        import json
        from urllib.parse import parse_qs, urlsplit

        from PIL import Image

        from gjodsel.farm_map import MapUnavailable

        self.calls.append(url)
        if self.offline:
            raise MapUnavailable("Fikk ikke kontakt med api.kartverket.no (test). Kartet krever internett.")
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if "eiendom" in parts.path:
            features = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": rings},
                         "properties": {"hovedområde": main, "nøyaktighetsklasseteig": accuracy}}
                        for rings, main, accuracy in self.teiger]
            return "application/json", json.dumps({"type": "FeatureCollection", "features": features}).encode()
        ar5 = "nibio" in parts.netloc
        if (ar5 and not self.ar5_ok) or (not ar5 and not self.topo_ok):
            return "text/xml", b"<ServiceExceptionReport/>"
        size = int(query["WIDTH"]), int(query["HEIGHT"])
        image = Image.new("RGBA", size, (90, 200, 90, 160) if ar5 else (235, 235, 228, 255))
        out = io.BytesIO()
        image.save(out, "PNG")
        return "image/png", out.getvalue()


@pytest.fixture
def fake_map_net():
    return FakeMapNetwork


# --- Synthetic NIBIO gårdskart (gk_enkel-like PDF) with a known position ---------------------------------

def make_gardskart_pdf(path, teiger_lonlat, centre_e, centre_n, zone=32, scale=4000, matrikkel="4640-12/3/0",
                       printed="01.04.2026", decoys=True):
    """A4 page like NIBIO's gårdskart: a JPEG 'photo' and a transparent overlay (SMask) with the property
    boundary in pink, drawn so that the map frame is centred on (centre_e, centre_n) in UTM `zone`.
    Image names and sizes come from reportlab, not from the real files."""
    import io
    import random

    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    from gjodsel.geo import wgs84_to_utm

    photo_rect = (20, 206.58, 555, 615.42)
    overlay_rect = (20, 206.13, 555, 615.87)
    overlay_px = (1003, 1113)
    m_per_pt = scale / (72 / 0.0254)
    e0 = centre_e - overlay_rect[2] / 2 * m_per_pt
    n0 = centre_n + overlay_rect[3] / 2 * m_per_pt
    mx = m_per_pt * overlay_rect[2] / overlay_px[0]
    my = m_per_pt * overlay_rect[3] / overlay_px[1]

    rng = random.Random(7)
    photo = Image.new("RGB", (1185, 1314))
    draw = ImageDraw.Draw(photo)
    for _ in range(400):  # fields and noise, so the photo is not empty
        x, y = rng.randrange(1185), rng.randrange(1314)
        draw.rectangle([x, y, x + rng.randrange(20, 160), y + rng.randrange(20, 160)],
                       fill=(rng.randrange(60, 200), rng.randrange(90, 210), rng.randrange(40, 140)))
    photo_file = Path(path).with_suffix(".photo.jpg")
    photo.save(photo_file, "JPEG", quality=80)

    overlay = Image.new("RGBA", overlay_px, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if decoys:
        for _ in range(12):  # AR5 lines in yellow, labels, the driftssenter dot
            draw.line([(rng.randrange(1003), rng.randrange(1113)), (rng.randrange(1003), rng.randrange(1113))],
                      fill=(255, 255, 0, 220), width=2)
        draw.ellipse([495, 550, 505, 560], fill=(255, 0, 0, 255))
        draw.text((520, 540), "178/1", fill=(0, 0, 0, 255))
    for rings, *_ in teiger_lonlat:
        for ring in rings:
            pts = [((e - e0) / mx, (n0 - n) / my) for e, n in (wgs84_to_utm(lat, lon, zone) for lon, lat, *_ in ring)]
            draw.line(pts, fill=(255, 153, 153, 255), width=3)

    canvas = Canvas(str(path), pagesize=(595, 842))
    canvas.drawImage(str(photo_file), *photo_rect)
    buffer = io.BytesIO()
    overlay.save(buffer, "PNG")
    canvas.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), *overlay_rect, mask="auto")
    canvas.setFont("Helvetica", 9)
    canvas.drawString(30, 160, "Målestokk 1: %d ved A4 utskrift" % scale)
    canvas.drawString(30, 148, f"Utskriftsdato: {printed} 10:00")
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(30, 120, f"GÅRDSKART  {matrikkel}")
    canvas.save()
    photo_file.unlink()
    return path


@pytest.fixture
def gardskart_pdf():
    return make_gardskart_pdf
