from dataclasses import replace

import pytest

from gjodsel.models import Application, Field, FieldSeason, SoilSample
from gjodsel.planning import (
    PFieldRow,
    application_supply,
    balance,
    balance_status,
    default_soil_sample,
    duplicate_field_numbers,
    field_input,
    legal_status,
    p_check,
    soil_check,
    soil_input,
    sum_supply,
)


def planned(code, kg):
    return Application(kind="planlagt", product_code=code, kg_per_daa=kg)


# --- Supply -----------------------------------------------------------------------------


def test_application_supply(fixed_ref):
    s = application_supply(fixed_ref.products["kalksalpeter"], 10)
    assert s["n"] == pytest.approx(1.55)
    assert s["ca"] == pytest.approx(1.9)
    assert s["p"] == 0


def test_template_supply(fixed_ref):
    template = [r for r in fixed_ref.templates if r.template == "Eple bærende standard"]
    total = sum_supply([planned(r.product_code, r.kg_per_daa) for r in template], fixed_ref)
    assert total["n"] == pytest.approx(2.7 + 1.55 + 1.55)   # OPTI-NS 10 kg + 2 × kalksalpeter 10 kg
    assert total["k"] == pytest.approx(10.0)                # patentkali 40 kg × 25 %
    assert total["mg"] == pytest.approx(2.4)
    assert total["s"] == pytest.approx(0.37 + 6.8)


def test_unknown_content_propagates(fixed_ref):
    total = sum_supply([planned("fg_12_4_18", 25), planned("kalksalpeter", 10)], fixed_ref)
    assert total["n"] == pytest.approx(3.0 + 1.55)
    assert total["mg"] is None  # Fullgjødsel has no Mg value in products.csv
    unknown = sum_supply([planned("finnes_ikke", 10)], fixed_ref)
    assert unknown["n"] is None


def test_balance_status():
    assert balance(7.0, 7.5) == pytest.approx(-0.5)
    assert balance(None, 7.5) is None
    assert balance_status(7.0, 7.5) == "ok"        # −6.7 %
    assert balance_status(6.0, 7.5) == "under"     # −20 %
    assert balance_status(8.5, 7.5) == "over"      # +13 %
    assert balance_status(0, 0) == "ok"
    assert balance_status(0.5, 0) == "over"
    assert balance_status(None, 1) is None


def test_effective_requirement_uses_override():
    season = FieldSeason(req_n=7.0, override_n=6.0, req_p=1.5)
    assert season.effective("n") == 6.0
    assert season.effective("p") == 1.5
    assert season.effective("k") is None


# --- §11 case 11: P check ------------------------------------------------------------------


def test_11_p_check(fixed_ref, settings):
    rows = [PFieldRow("1", 10, 3.0), PFieldRow("2", 10, 2.0)]
    check = p_check(rows, "standard", 2027, fixed_ref, settings)
    assert check.limit == 2.8
    assert check.field_limit == pytest.approx(3.08)
    assert check.average == pytest.approx(2.5)
    assert check.ok is True and check.breaches == []

    rows[0] = PFieldRow("1", 10, 3.2)
    check = p_check(rows, "standard", 2027, fixed_ref, settings)
    assert check.average_ok  # 2.6 ≤ 2.8
    assert [r.label for r in check.breaches] == ["1"]
    assert check.ok is False
    assert "over maks per skifte" in check.messages[0]


def test_p_check_average_breach(fixed_ref, settings):
    check = p_check([PFieldRow("1", 10, 3.0), PFieldRow("2", 10, 3.0)], "standard", 2027, fixed_ref, settings)
    assert check.average_ok is False and check.ok is False
    assert "over grensen" in check.messages[0]


def test_p_check_region_and_year(fixed_ref, settings):
    assert p_check([], "rogaland", 2027, fixed_ref, settings).limit == 3.1
    assert p_check([], "standard", 2026, fixed_ref, settings).limit == 3.5
    missing = p_check([PFieldRow("1", 10, 1)], "vestland", 2027, fixed_ref, settings)
    assert missing.limit is None and missing.ok is None
    assert "Ingen P-grense" in missing.messages[0]


def test_p_check_unknown_p(fixed_ref, settings):
    check = p_check([PFieldRow("1", 10, None)], "standard", 2027, fixed_ref, settings)
    assert check.ok is None


# --- §11 case 12: legal status -------------------------------------------------------------


def test_12_legal_status(settings):
    assert legal_status(25.0, settings).plan_required is False
    assert legal_status(25.0, settings).text == "Ikke påkrevd (≤ 25 daa) – gjødseljournal er likevel påkrevd"
    assert legal_status(25.1, settings).plan_required is True
    assert legal_status(25.1, settings).text == "Gjødslingsplan påkrevd (> 25 daa)"


# --- Soil checks and helpers --------------------------------------------------------------------


def test_soil_check(settings):
    rows = [
        ("1 Eple", True, SoilSample(sample_date="2025-04-01", method="AL")),
        ("2 Pære", True, None),
        ("3 Plomme", True, SoilSample(sample_date="2018-04-01", method="AL")),   # 8 years
        ("4 Gammel", True, SoilSample(sample_date="2010-04-01", method="AL")),
        ("5 Brakk", False, None),                                                 # not fertilized
    ]
    check = soil_check(rows, 2026, settings)
    assert check.missing == ["2 Pære", "4 Gammel"]
    assert check.expiring_soon == ["3 Plomme"]


def test_default_soil_sample():
    samples = [
        SoilSample(id=1, sample_date="2016-04-01", method="AL"),
        SoilSample(id=2, sample_date="2024-04-01", method="annet"),
        SoilSample(id=3, sample_date="2021-04-01", method="AL"),
    ]
    assert default_soil_sample(samples, 2026, 8).id == 3
    assert default_soil_sample(samples[:2], 2026, 8) is None


def test_duplicate_field_numbers():
    fields = [Field(field_no="1"), Field(field_no="2"), Field(field_no=" 1 ")]
    assert duplicate_field_numbers(fields) == ["1"]


def test_input_conversion():
    f = Field(area_daa=10.8, culture_code="eple", planting_year=2017)
    fi = field_input(f, FieldSeason(expected_yield_kg_daa=3000, growth_class="lt20"))
    assert (fi.area_daa, fi.expected_yield_kg_daa, fi.growth_class) == (10.8, 3000, "lt20")
    sample = SoilSample(sample_date="2025-04-01", method="AL", p_al=8, k_al=10, mold_pct=4)
    si = soil_input(sample)
    assert (si.p_al, si.k_al, si.mold_pct, si.method) == (8, 10, 4, "AL")
    assert soil_input(None) is None
    assert replace(si, k_hno3=50).k_hno3 == 50
