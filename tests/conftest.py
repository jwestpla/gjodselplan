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
