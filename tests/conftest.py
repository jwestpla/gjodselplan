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
