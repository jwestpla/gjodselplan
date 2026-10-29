import pytest

from gjodsel import repo
from gjodsel.models import Enterprise, Field, Property
from gjodsel.validation import ValidationError


def _plan_with_field(conn, enterprise_id, field_id):
    plan = conn.execute("INSERT INTO plan (enterprise_id, year) VALUES (?, 2026)", (enterprise_id,)).lastrowid
    conn.execute("INSERT INTO field_season (plan_id, field_id) VALUES (?, ?)", (plan, field_id))


# --- Enterprise ---------------------------------------------------------------------


def test_enterprise_crud(conn):
    e = repo.save_enterprise(conn, Enterprise(
        name="  Fruktgarden AS ", org_nr="923 609 016", phone="+47 123 45 678", postcode="6887", email="",
    ))
    assert e.id and e.name == "Fruktgarden AS"
    assert e.org_nr == "923609016"
    assert e.phone == "12345678"
    assert e.email is None  # empty -> NULL, so UNIQUE/queries behave
    assert e.created_at

    e.place = "Lærdal"
    updated = repo.save_enterprise(conn, e)
    assert updated.place == "Lærdal"
    assert updated.created_at == e.created_at

    assert [x.name for x in repo.list_enterprises(conn, "lærdal")] == ["Fruktgarden AS"]
    assert repo.list_enterprises(conn, "finnes ikke") == []

    repo.delete_enterprise(conn, e.id)
    assert repo.get_enterprise(conn, e.id) is None


def test_enterprise_validation(conn):
    with pytest.raises(ValidationError) as exc:
        repo.save_enterprise(conn, Enterprise(
            name="", org_nr="923609017", postcode="123", phone="123", email="x@y", region="",
        ))
    text = "\n".join(exc.value.errors)
    for fragment in ("Foretaksnavn", "Org.nr.", "Postnummer", "Telefonnummer", "E-post", "Region"):
        assert fragment in text


def test_enterprise_unknown_region(conn, ref):
    regions = {r.region for r in ref.p_limits}
    with pytest.raises(ValidationError, match="Region «vestland»"):
        repo.save_enterprise(conn, Enterprise(name="X", region="vestland"), regions)


def test_two_enterprises_without_org_nr(conn):
    repo.save_enterprise(conn, Enterprise(name="A", org_nr=""))
    repo.save_enterprise(conn, Enterprise(name="B", org_nr=" "))
    assert len(repo.list_enterprises(conn)) == 2


def test_duplicate_org_nr_refused(conn, farm):
    with pytest.raises(ValidationError, match="allerede registrert med org.nr"):
        repo.save_enterprise(conn, Enterprise(name="Kopi", org_nr="923609016"))


def test_delete_enterprise_cascades_without_plans(conn, farm):
    e, p, f = farm
    conn.execute("INSERT INTO soil_sample (enterprise_id, sample_date) VALUES (?, '2025-04-01')", (e.id,))
    assert repo.enterprise_dependents(conn, e.id) == {
        "properties": 1, "fields": 1, "soil_samples": 1, "plans": 0,
    }
    repo.delete_enterprise(conn, e.id)
    for table in ("enterprise", "property", "field", "soil_sample"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_delete_enterprise_with_plan_refused(conn, farm):
    e, p, f = farm
    _plan_with_field(conn, e.id, f.id)
    with pytest.raises(ValidationError, match="gjødslingsplaner"):
        repo.delete_enterprise(conn, e.id)
    assert repo.get_enterprise(conn, e.id) is not None
    assert repo.get_field(conn, f.id) is not None  # nothing half-deleted


# --- Property ---------------------------------------------------------------------------


def test_property_matrikkel_and_label():
    p = Property(kommunenr="4642", gnr=12, bnr=3, farm_name="Øvre")
    assert p.matrikkel == "4642-12/3"
    assert p.label == "Øvre (4642-12/3)"
    assert Property(kommunenr="4642", gnr=12, bnr=3, fnr=1).matrikkel == "4642-12/3/1"
    assert Property(kommunenr="4642", gnr=12, bnr=3, snr=2).matrikkel == "4642-12/3/0/2"


def test_property_validation(conn, farm):
    e, _, _ = farm
    with pytest.raises(ValidationError) as exc:
        repo.save_property(conn, Property(enterprise_id=e.id, kommunenr="46", gnr=0, bnr=None, fnr=-1,
                                          tenure="leiet"))
    text = "\n".join(exc.value.errors)
    for fragment in ("Kommunenummer", "gnr", "bnr", "fnr", "Eierform"):
        assert fragment in text


def test_duplicate_property_refused(conn, farm):
    e, _, _ = farm
    with pytest.raises(ValidationError, match="allerede registrert"):
        repo.save_property(conn, Property(enterprise_id=e.id, kommunenr="4642", gnr=12, bnr=3))
    # Same property on another enterprise (e.g. leased) is allowed.
    other = repo.save_enterprise(conn, Enterprise(name="Leietaker"))
    repo.save_property(conn, Property(enterprise_id=other.id, kommunenr="4642", gnr=12, bnr=3, tenure="leid"))


def test_delete_property_with_planned_field_refused(conn, farm):
    e, p, f = farm
    _plan_with_field(conn, e.id, f.id)
    with pytest.raises(ValidationError, match="gjødslingsplaner"):
        repo.delete_property(conn, p.id)


def test_delete_property_deletes_its_fields(conn, farm):
    _, p, f = farm
    assert repo.property_field_count(conn, p.id) == 1
    repo.delete_property(conn, p.id)
    assert repo.get_field(conn, f.id) is None


def test_set_property_map(conn, farm):
    _, p, _ = farm
    repo.set_property_map(conn, p.id, "attachments/1/kart.png")
    assert repo.get_property(conn, p.id).map_attachment == "attachments/1/kart.png"


# --- Field ------------------------------------------------------------------------------------


def test_field_crud_and_natural_sort(conn, farm, ref):
    e, p, f = farm
    assert f.active is True and f.area_daa == 10.8
    for no in ("10", "2"):
        repo.save_field(conn, Field(property_id=p.id, field_no=no, name=f"Skifte {no}", area_daa=1,
                                    culture_code="plomme"), ref.cultures.keys())
    assert [x.field_no for x in repo.list_fields(conn, e.id)] == ["1", "2", "10"]

    f.variety = "Aroma"
    assert repo.save_field(conn, f).variety == "Aroma"


def test_duplicate_field_no_refused(conn, farm):
    e, p, f = farm
    with pytest.raises(ValidationError, match="Skiftenummer «1» finnes allerede"):
        repo.save_field(conn, Field(property_id=p.id, field_no=" 1 ", name="Kopi", area_daa=1,
                                    culture_code="eple"))
    # Also across properties of the same enterprise.
    p2 = repo.save_property(conn, Property(enterprise_id=e.id, kommunenr="4642", gnr=12, bnr=4))
    with pytest.raises(ValidationError, match="finnes allerede"):
        repo.save_field(conn, Field(property_id=p2.id, field_no="1", name="Kopi", area_daa=1,
                                    culture_code="eple"))
    # Saving the field itself again is fine.
    repo.save_field(conn, f)


def test_same_field_no_on_other_enterprise_allowed(conn, farm):
    other = repo.save_enterprise(conn, Enterprise(name="Nabogård"))
    p = repo.save_property(conn, Property(enterprise_id=other.id, kommunenr="4642", gnr=1, bnr=1))
    repo.save_field(conn, Field(property_id=p.id, field_no="1", name="Eple", area_daa=2, culture_code="eple"))


def test_field_cannot_move_to_other_enterprise(conn, farm):
    _, _, f = farm
    other = repo.save_enterprise(conn, Enterprise(name="Nabogård"))
    p = repo.save_property(conn, Property(enterprise_id=other.id, kommunenr="4642", gnr=1, bnr=1))
    f.property_id = p.id
    with pytest.raises(ValidationError, match="annet foretak"):
        repo.save_field(conn, f)


def test_field_validation(conn, farm, ref):
    _, p, _ = farm
    with pytest.raises(ValidationError) as exc:
        repo.save_field(conn, Field(property_id=p.id, field_no="", name="", area_daa=0, culture_code="banan",
                                    planting_year=1800, row_spacing_m=0, lat=61.1), ref.cultures.keys())
    text = "\n".join(exc.value.errors)
    for fragment in ("Skiftenummer", "Skiftenavn", "Areal", "«banan»", "Planteår", "Radavstand",
                     "breddegrad og lengdegrad"):
        assert fragment in text


def test_trees_per_daa():
    assert Field(row_spacing_m=3.5, plant_spacing_m=1.0).trees_per_daa == pytest.approx(285.71, abs=0.01)
    assert Field(row_spacing_m=3.5).trees_per_daa is None


def test_field_with_plan_cannot_be_deleted_but_can_be_deactivated(conn, farm):
    e, _, f = farm
    _plan_with_field(conn, e.id, f.id)
    assert repo.field_has_plans(conn, f.id)
    with pytest.raises(ValidationError, match="Deaktiver"):
        repo.delete_field(conn, f.id)
    repo.set_field_active(conn, f.id, False)
    assert repo.get_field(conn, f.id).active is False
    assert repo.list_fields(conn, e.id, include_inactive=False) == []


def test_delete_field_without_plan(conn, farm):
    _, _, f = farm
    repo.delete_field(conn, f.id)
    assert repo.get_field(conn, f.id) is None


# --- Settings and raw data ----------------------------------------------------------------------


def test_settings_roundtrip(conn):
    repo.save_settings(conn, {"advisor_name": "Kari Rådgiver", "p_skew_factor": "1.10"})
    settings = repo.get_settings(conn)
    assert settings["advisor_name"] == "Kari Rådgiver"
    assert settings["soil_max_age_years"] == "8"


def test_read_table_rejects_unknown_table(conn):
    with pytest.raises(ValueError):
        repo.read_table(conn, "enterprise; DROP TABLE field")
    columns, rows = repo.read_table(conn, "setting")
    assert columns == ["key", "value"] and rows
