from datetime import date

import pytest

from gjodsel import repo
from gjodsel.models import Enterprise, Field, Property, SoilSample
from gjodsel.soil import (
    P_AL_UNIT_P,
    P_AL_UNIT_P2O5,
    classify,
    p_al_to_element,
    sample_validity,
    soil_notes,
)
from gjodsel.units import format_date, parse_date
from gjodsel.validation import ValidationError


def test_p2o5_entry_stores_element_value():
    # PLAN §10 phase 2 acceptance: 57 mg P2O5/100 g -> 24.9 mg P/100 g
    assert p_al_to_element(57, P_AL_UNIT_P2O5) == 24.9
    assert p_al_to_element(8.0, P_AL_UNIT_P) == 8.0
    assert p_al_to_element(None, P_AL_UNIT_P2O5) is None
    with pytest.raises(ValueError):
        p_al_to_element(5, "ppm")


def test_p2o5_value_round_trips_through_repo(conn, farm):
    e, _, f = farm
    s = repo.save_soil_sample(conn, SoilSample(
        enterprise_id=e.id, sample_date="2025-04-01", p_al=p_al_to_element(57, P_AL_UNIT_P2O5),
        lat=61.1, lon=7.48,
    ), [f.id])
    assert repo.get_soil_sample(conn, s.id).p_al == 24.9


@pytest.mark.parametrize("method, sample_year, plan_year, valid, soon", [
    ("AL", 2025, 2026, True, False),
    ("AL", 2018, 2026, True, True),     # 8 years: still valid, expires next year
    ("AL", 2017, 2026, False, False),   # 9 years: expired
    ("annet", 2025, 2026, False, False),
    ("AL", 2027, 2026, False, False),   # taken after the plan year
])
def test_sample_validity(method, sample_year, plan_year, valid, soon):
    v = sample_validity(method, sample_year, plan_year, max_age_years=8)
    assert (v.valid, v.expires_soon) == (valid, soon)
    assert bool(v.reason) is not valid


def test_classify_against_optimum(ref):
    ph = ref.soil_optimum["ph"]  # 5.5–6.5
    assert classify(5.8, ph) == "ok"
    assert classify(5.5, ph) == "ok"   # limits inclusive
    assert classify(6.5, ph) == "ok"
    assert classify(5.2, ph) == "lav"
    assert classify(7.0, ph) == "høy"
    assert classify(None, ph) is None
    assert classify(5.0, None) is None


def test_soil_notes(ref):
    assert soil_notes({"ph": 5.8}, ref.soil_optimum) == []
    notes = soil_notes({"ph": 5.2, "p_al": 24.9, "k_al": 25}, ref.soil_optimum)
    assert notes == [
        "pH 5,2 under optimum 5,5–6,5 – vurder kalking",
        "P-AL 24,9 over optimum 8–12",
    ]


def test_parse_and_format_date():
    assert parse_date("01.04.2025") == date(2025, 4, 1)
    assert parse_date("1.4.25") == date(2025, 4, 1)
    assert parse_date("2025-04-01") == date(2025, 4, 1)
    assert parse_date("  ") is None
    with pytest.raises(ValueError):
        parse_date("31.02.2025")
    assert format_date("2025-04-01") == "01.04.2025"


def test_save_sample_with_links(conn, farm):
    e, p, f = farm
    f2 = repo.save_field(conn, Field(property_id=p.id, field_no="2", name="Plommer", area_daa=4,
                                     culture_code="plomme"))
    s = repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", ph=6.2),
                              [f.id, f2.id])
    assert repo.sample_field_links(conn, e.id) == {s.id: [f.id, f2.id]}
    assert [x.id for x in repo.samples_for_field(conn, f2.id)] == [s.id]

    repo.save_soil_sample(conn, s, [f2.id])  # replace links
    assert repo.sample_field_links(conn, e.id) == {s.id: [f2.id]}
    repo.save_soil_sample(conn, s)  # field_ids=None keeps links
    assert repo.sample_field_links(conn, e.id) == {s.id: [f2.id]}


def test_samples_for_field_newest_first(conn, farm):
    e, _, f = farm
    for d in ("2019-05-01", "2024-05-01", "2021-05-01"):
        repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date=d), [f.id])
    assert [s.sample_date for s in repo.samples_for_field(conn, f.id)] == ["2024-05-01", "2021-05-01", "2019-05-01"]


def test_cannot_link_field_of_other_enterprise(conn, farm):
    e, _, _ = farm
    other = repo.save_enterprise(conn, Enterprise(name="Nabogård"))
    p = repo.save_property(conn, Property(enterprise_id=other.id, kommunenr="4642", gnr=1, bnr=1))
    foreign = repo.save_field(conn, Field(property_id=p.id, field_no="9", name="X", area_daa=1,
                                          culture_code="eple"))
    with pytest.raises(ValidationError, match="samme foretak"):
        repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01"), [foreign.id])
    assert repo.list_soil_samples(conn, e.id) == []  # rolled back


def test_soil_sample_validation(conn, farm):
    e, _, _ = farm
    with pytest.raises(ValidationError) as exc:
        repo.save_soil_sample(conn, SoilSample(
            enterprise_id=e.id, sample_date="2999-01-01", method="Mehlich", depth_from_cm=20, depth_to_cm=10,
            ph=15, p_al=-1, mold_pct=120, lon=7.0,
        ))
    text = "\n".join(exc.value.errors)
    for fragment in ("fram i tid", "Metode", "Dybde til", "pH", "P-AL", "Moldinnhold", "breddegrad og lengdegrad"):
        assert fragment in text
    with pytest.raises(ValidationError, match="Prøvedato mangler"):
        repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date=""))


def test_sample_used_in_plan_cannot_be_deleted(conn, farm):
    e, _, f = farm
    s = repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01"), [f.id])
    plan = conn.execute("INSERT INTO plan (enterprise_id, year) VALUES (?, 2026)", (e.id,)).lastrowid
    conn.execute("INSERT INTO field_season (plan_id, field_id, soil_sample_id) VALUES (?, ?, ?)",
                 (plan, f.id, s.id))
    assert repo.soil_sample_in_use(conn, s.id)
    with pytest.raises(ValidationError, match="brukt i en gjødslingsplan"):
        repo.delete_soil_sample(conn, s.id)


def test_delete_sample_removes_links(conn, farm):
    e, _, f = farm
    s = repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01"), [f.id])
    repo.delete_soil_sample(conn, s.id)
    assert repo.sample_field_links(conn, e.id) == {}


def test_load_settings(conn):
    settings = repo.load_settings(conn)
    assert settings.soil_max_age_years == 8
    assert settings.p_skew_factor == 1.10
    assert settings.plan_threshold_daa == 25
    assert settings.apply_mold_correction is False

    repo.save_settings(conn, {"soil_max_age_years": "8,5", "p_skew_factor": "", "apply_mold_correction": "ja"})
    with pytest.raises(ValidationError) as exc:
        repo.load_settings(conn)
    text = "\n".join(exc.value.errors)
    assert "heltall" in text and "p_skew_factor" in text and "apply_mold_correction" in text
