"""PLAN §11 calculation cases, on the frozen fixture reference data ("Testgård")."""

from dataclasses import fields, replace

import pytest

from gjodsel.calc import NO_VALID_SOIL, UNVERIFIED_PREFIX, FieldInput, SoilInput, calculate_requirement
from gjodsel.reference import RefTables
from gjodsel.soil import P_AL_UNIT_P2O5, p_al_to_element

PLAN_YEAR = 2026
APPLE = FieldInput(area_daa=10.8, culture_code="eple", planting_year=2017, expected_yield_kg_daa=2000,
                   growth_class="20to40")
SOIL = SoilInput(sample_date="2025-04-01", method="AL", p_al=8, k_al=10, ph=6.5)


@pytest.fixture
def calc(fixed_ref, settings):
    def run(field=APPLE, soil=SOIL, year=PLAN_YEAR, ref=fixed_ref, settings=settings):
        return calculate_requirement(field, soil, year, ref, settings)
    return run


def warned(req, fragment):
    return any(fragment in w for w in req.warnings)


# --- §11 cases ------------------------------------------------------------------------


def test_01_bearing_apple(calc):
    req = calc()
    assert req.stage == "bærende"
    assert (req.n, req.p, req.k) == (4.0, 1.5, 7.5)
    assert req.soil_valid
    assert warned(req, "K-HNO₃ mangler – bruker raden for 50–80")
    assert req.rationale == (
        "N: norm 4,0 + avling 2000 kg (±0) + nyvekst 20–40 cm (±0) = 4,0 kg/daa. "
        "P: norm 1,5, P-AL 8 (4,5–9,5) ±0 % = 1,5 kg/daa. "
        "K: norm 6,0, K-AL 10, K-HNO₃ mangler (antatt 50–80) +25 % = 7,5 kg/daa."
    )


def test_02_high_p_al_fertilab(calc):
    p_al = p_al_to_element(57, P_AL_UNIT_P2O5)
    assert p_al == 24.9
    req = calc(soil=replace(SOIL, p_al=p_al))
    assert req.p == 0.4  # 1.5 × 0.25 = 0.375
    assert "P-AL 24,9 (≥15,5) −75 %" in req.rationale


def test_03_high_yield(calc):
    req = calc(field=replace(APPLE, expected_yield_kg_daa=3500))
    assert req.n == 7.0  # 4.0 + 1.5 × 2.0
    assert "avling 3500 kg (+3,0)" in req.rationale


def test_04_planting_year(calc, fixed_ref):
    req = calc(field=replace(APPLE, planting_year=PLAN_YEAR))
    age = fixed_ref.age_factor(0)
    assert req.stage == age.stage == "planteår"
    assert not any(s.label_nb.startswith("avling") for s in req.steps)
    assert req.n == pytest.approx(round(4.0 * age.n_factor, 1))       # 3.0
    assert req.p == 2.3                                                # 1.5 × 1.5 = 2.25 -> 2.3
    assert req.k == 3.8                                                # 6.0 × 1.25 × 0.5 = 3.75 -> 3.8
    assert "× 0,75 (planteår)" in req.rationale


def test_04b_young_tree(calc):
    req = calc(field=replace(APPLE, planting_year=PLAN_YEAR - 2))
    assert req.stage == "ungtre"
    assert req.n == 3.0  # (4.0 + 0) × 0.75, no yield adjustment


def test_05_expired_sample(calc):
    req = calc(soil=replace(SOIL, sample_date="2017-04-01"))  # 9 years old
    assert not req.soil_valid
    assert (req.p, req.k) == (1.5, 6.0)  # base norms, no correction
    assert warned(req, NO_VALID_SOIL)
    assert warned(req, "utløpt")
    assert "ingen jordkorreksjon (mangler gyldig jordprøve)" in req.rationale


def test_06_non_al_sample(calc):
    req = calc(soil=replace(SOIL, method="annet", p_al=24.9))
    assert not req.soil_valid
    assert (req.p, req.k) == (1.5, 6.0)
    assert warned(req, "ikke AL-metode")


def test_06b_missing_sample(calc):
    req = calc(soil=None)
    assert not req.soil_valid
    assert (req.p, req.k) == (1.5, 6.0)
    assert warned(req, NO_VALID_SOIL)
    assert req.soil_notes == []


def test_07_growth_below_20(calc, fixed_ref):
    req = calc(field=replace(APPLE, growth_class="lt20"))
    assert req.n == 5.0
    growth = next(s for s in req.steps if s.label_nb.startswith("nyvekst"))
    assert growth.delta == fixed_ref.n_growth_adjustment["lt20"].n_adj == 1.0


def test_08_mold_correction(calc, settings):
    soil = replace(SOIL, mold_pct=2.5)
    on = calc(soil=soil, settings=replace(settings, apply_mold_correction=True))
    assert on.n == 6.0  # +2.0
    assert "moldinnhold 2,5 % (+2,0)" in on.rationale
    off = calc(soil=soil)
    assert off.n == 4.0


def test_08b_mold_on_but_unknown(calc, settings):
    req = calc(settings=replace(settings, apply_mold_correction=True))
    assert req.n == 4.0
    assert warned(req, "moldinnhold mangler")


def test_09_clamping(calc, fixed_ref):
    # N: 4.0 + (0 − 2000)/1000 × 2.0 − 1.0 = −1.0 -> 0
    req = calc(field=replace(APPLE, expected_yield_kg_daa=0, growth_class="gt40"))
    assert req.n == 0.0
    assert any(s.kind == "clamp" and s.nutrient == "N" for s in req.steps)
    # P and K with a (hypothetical) correction below −100 %
    harsh = replace(
        fixed_ref,
        p_al_correction=tuple(replace(b, pct=-150) for b in fixed_ref.p_al_correction),
        k_correction=tuple(replace(c, pct=-150) for c in fixed_ref.k_correction),
    )
    req = calc(ref=harsh)
    assert (req.p, req.k) == (0.0, 0.0)
    assert min(req.n, req.p, req.k) >= 0


def test_10_ph_notes(calc):
    assert not any(n.startswith("pH") for n in calc(soil=replace(SOIL, ph=5.8)).soil_notes)
    notes = calc(soil=replace(SOIL, ph=5.2)).soil_notes
    assert "pH 5,2 under optimum 5,5–6,5 – vurder kalking" in notes


def test_13_unverified_warning(calc, fixed_ref):
    req = calc(field=replace(APPLE, culture_code="paere"))
    assert warned(req, UNVERIFIED_PREFIX)
    assert warned(req, "cultures.csv (paere)")
    assert ("cultures.csv", fixed_ref.cultures["paere"]) in req.unverified_rows

    req = calc(ref=_all_verified(fixed_ref))
    assert not warned(req, UNVERIFIED_PREFIX)
    assert req.unverified_rows == []


# --- Further behaviour ------------------------------------------------------------------


def test_unknown_culture(calc):
    req = calc(field=replace(APPLE, culture_code="banan"))
    assert (req.n, req.p, req.k) == (None, None, None)
    assert warned(req, "«banan» finnes ikke")
    assert req.rationale == ""


def test_missing_planting_year_assumes_bearing(calc):
    req = calc(field=replace(APPLE, planting_year=None))
    assert req.stage == "bærende"
    assert warned(req, "Planteår mangler – antar bærende")


def test_planting_year_after_plan_year(calc):
    req = calc(field=replace(APPLE, planting_year=PLAN_YEAR + 1))
    assert req.n is None
    assert warned(req, "etter planåret")


def test_missing_growth_class_warns(calc):
    req = calc(field=replace(APPLE, growth_class=None))
    assert req.n == 4.0
    assert warned(req, "Nyvekstklasse er ikke satt")


def test_missing_expected_yield_uses_culture_default(calc, fixed_ref):
    req = calc(field=replace(APPLE, expected_yield_kg_daa=None))
    default = fixed_ref.cultures["eple"].default_yield_kg_daa  # 3500
    assert req.n == 7.0
    assert warned(req, f"standard {int(default)} kg/daa")


def test_k_hno3_known(calc):
    req = calc(soil=replace(SOIL, k_hno3=100))  # row 80–110, K-AL 6.5–10.5: 0 %
    assert req.k == 6.0
    assert not warned(req, "K-HNO₃ mangler")
    assert "K-AL 10, K-HNO₃ 100 ±0 %" in req.rationale


def test_sample_missing_p_al(calc):
    req = calc(soil=replace(SOIL, p_al=None))
    assert req.p == 1.5
    assert warned(req, "mangler P-AL")


def test_sample_expiring_soon(calc):
    req = calc(soil=replace(SOIL, sample_date="2018-04-01"))  # 8 years: last valid year
    assert req.soil_valid
    assert warned(req, "utløper snart")


def test_reference_rows_used(calc, fixed_ref):
    files = [f for f, _ in calc().reference_rows]
    assert files == ["cultures.csv", "age_factors.csv", "n_growth_adjustment.csv",
                     "p_al_correction.csv", "k_correction.csv"]


@pytest.mark.parametrize("culture", ["eple", "paere", "sotkirsebaer", "surkirsebaer", "plomme"])
def test_bundled_reference_calculates_every_culture(ref, settings, culture):
    req = calculate_requirement(replace(APPLE, culture_code=culture), SOIL, PLAN_YEAR, ref, settings)
    assert None not in (req.n, req.p, req.k)


def _all_verified(ref: RefTables) -> RefTables:
    def verify(value):
        if isinstance(value, dict):
            return {k: verify(v) for k, v in value.items()}
        if isinstance(value, tuple):
            return tuple(verify(v) for v in value)
        return replace(value, source="Verifisert (test)") if hasattr(value, "source") else value
    return RefTables(**{f.name: verify(getattr(ref, f.name)) for f in fields(ref)})
