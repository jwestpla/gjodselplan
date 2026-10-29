from dataclasses import replace

import pytest

from gjodsel import plan_service as ps
from gjodsel import repo
from gjodsel.models import Application, Field, SoilSample
from gjodsel.plan_view import PlanView
from gjodsel.validation import ValidationError


@pytest.fixture
def setup(conn, farm, fixed_ref, settings):
    """Testgård: field 1 apple (planted 2017, valid sample), field 2 plum planted 2026 (no sample)."""
    e, p, f1 = farm
    f2 = repo.save_field(conn, Field(property_id=p.id, field_no="2", name="Plommer", area_daa=4.0,
                                     culture_code="plomme", planting_year=2026))
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", p_al=8, k_al=10,
                                           ph=6.5, lat=61.1, lon=7.5), [f1.id])
    return conn, fixed_ref, settings, e, f1, f2


def _season(conn, plan, field_id):
    return next(s for s in repo.list_field_seasons(conn, plan.id) if s.field_id == field_id)


def test_create_plan_defaults_and_requirements(setup):
    conn, ref, settings, e, f1, f2 = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2026)
    s1, s2 = _season(conn, plan, f1.id), _season(conn, plan, f2.id)
    assert s1.expected_yield_kg_daa == 3500           # default_yield from cultures.csv
    assert s1.forgrode == "Eple"                       # same culture, planted earlier
    assert s2.forgrode is None                         # planted this year
    assert s1.soil_sample_id is not None and s2.soil_sample_id is None
    # stored requirement (yield 3500, growth class not set): N 7.0, P 1.5, K 7.5
    assert (s1.req_n, s1.req_p, s1.req_k) == (7.0, 1.5, 7.5)
    assert s1.rationale.startswith("N: norm 4,0 + avling 3500 kg (+3,0)")
    with pytest.raises(ValidationError, match="finnes allerede"):
        ps.create_plan(conn, ref, settings, e.id, 2026)


def test_save_season_recalculates_and_requires_override_reason(setup):
    conn, ref, settings, e, f1, _ = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2026)
    s1 = _season(conn, plan, f1.id)
    saved = ps.save_season(conn, ref, settings, replace(s1, expected_yield_kg_daa=2000, growth_class="lt20"))
    assert saved.req_n == 5.0
    with pytest.raises(ValidationError, match="Begrunnelse"):
        ps.save_season(conn, ref, settings, replace(saved, override_n=6.0))
    saved = ps.save_season(conn, ref, settings, replace(saved, override_n=6.0, override_reason="Svak vekst i fjor"))
    assert saved.effective("n") == 6.0 and saved.req_n == 5.0
    with pytest.raises(ValidationError, match="Nyvekstklasse"):
        ps.save_season(conn, ref, settings, replace(saved, growth_class="xx"))


def test_template_supply_balance_and_view(setup):
    conn, ref, settings, e, f1, f2 = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2026)
    s1 = _season(conn, plan, f1.id)
    ps.apply_template(conn, ref, [s1.id], "Eple bærende standard")
    view = ps.build_view(conn, ref, settings, plan)
    row = view.row(s1.id)
    assert [line.product_code for line in row.planned] == ["opti_ns_27", "patentkali", "kalksalpeter", "kalksalpeter"]
    assert row.planned_supply["n"] == pytest.approx(5.8)
    assert row.planned[1].total_kg == pytest.approx(40 * 10.8)
    assert row.balance["n"] == pytest.approx(5.8 - 7.0)
    assert row.balance_status["n"] == "under"            # −17 %
    assert row.balance_status["k"] == "over"             # 10.0 vs 7.5
    assert view.fertilized_area == pytest.approx(14.8)
    assert view.plan_required is False
    assert view.p_ok is True
    # Plum field has no valid soil sample -> enterprise-level warning
    assert view.soil_missing == ["2 Plommer"]
    assert any("Kartskisse mangler" in w for w in view.warnings)
    unverified = next(w for w in view.warnings if w.startswith(ps.UNVERIFIED_WARNING))
    assert "products.csv (opti_ns_27, patentkali, kalksalpeter)" in unverified  # grouped per file
    # Applying again replaces instead of adding
    ps.apply_template(conn, ref, [s1.id], "Eple bærende standard")
    assert len(repo.list_applications(conn, plan.id, "planlagt")) == 4


def test_p_over_field_limit_in_view(setup):
    conn, ref, settings, e, f1, _ = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2027)
    s1 = _season(conn, plan, f1.id)
    # OPTI-P 0-20-0: 20 kg -> 4.0 kg P/daa, above 2.8 × 1.10
    ps.save_application(conn, ref, Application(field_season_id=s1.id, product_code="opti_p_20", kg_per_daa=20,
                                               timing_code="tidlig_vaar"))
    view = ps.build_view(conn, ref, settings, plan)
    assert view.row(s1.id).p_over_field_limit is True
    assert view.p_ok is False
    text, problem = ps.status_summary(conn, ref, settings, e.id, 2027)
    assert "P-sjekk BRUDD" in text and problem


def test_add_missing_fields(setup):
    conn, ref, settings, e, f1, f2 = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2026)
    f3 = repo.save_field(conn, Field(property_id=f1.property_id, field_no="3", name="Ny", area_daa=2,
                                     culture_code="eple", planting_year=2020))
    assert ps.add_missing_fields(conn, ref, settings, plan) == 1
    assert ps.add_missing_fields(conn, ref, settings, plan) == 0
    assert _season(conn, plan, f3.id).req_n is not None


def test_finalize_freezes_plan(setup):
    conn, ref, settings, e, f1, _ = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2026)
    s1 = _season(conn, plan, f1.id)
    ps.apply_template(conn, ref, [s1.id], "Eple bærende standard")
    plan = ps.finalize(conn, ref, settings, plan)
    assert plan.finished and plan.finalized_at
    snapshot = PlanView.from_json(plan.snapshot_json)
    assert snapshot.status == "ferdig"
    assert {r["file"] for r in snapshot.reference_rows} >= {"cultures.csv", "p_limits.csv", "products.csv"}

    # Read-only: season inputs and planned applications are refused ...
    with pytest.raises(ValidationError, match="ferdigstilt"):
        ps.save_season(conn, ref, settings, replace(s1, growth_class="lt20"))
    with pytest.raises(ValidationError, match="ferdigstilt"):
        ps.apply_template(conn, ref, [s1.id], "Nyplanting")
    # ... but the journal stays editable
    done = ps.save_application(conn, ref, Application(field_season_id=s1.id, kind="utfort", product_code="kalksalpeter",
                                                      kg_per_daa=12, date="2026-05-02"))
    # Later changes to master data don't change the finished plan
    repo.save_field(conn, replace(repo.get_field(conn, f1.id), area_daa=99))
    view = ps.build_view(conn, ref, settings, plan)
    assert view.row(s1.id).area_daa == 10.8
    assert [a.id for a in view.row(s1.id).actual] == [done.id]  # journal overlaid live
    with pytest.raises(ValidationError):
        ps.finalize(conn, ref, settings, plan)


def test_new_version_moves_journal(setup):
    conn, ref, settings, e, f1, _ = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2026)
    s1 = _season(conn, plan, f1.id)
    ps.save_season(conn, ref, settings, replace(s1, override_k=5, override_reason="Høy K i bladanalyse"))
    ps.apply_template(conn, ref, [s1.id], "Eple bærende standard")
    ps.save_application(conn, ref, Application(field_season_id=s1.id, kind="utfort", product_code="kalksalpeter",
                                               kg_per_daa=10, date="2026-04-20"))
    plan = ps.finalize(conn, ref, settings, plan)
    with pytest.raises(ValidationError, match="begrunnelse"):
        ps.new_version(conn, ref, settings, plan, " ")
    v2 = ps.new_version(conn, ref, settings, plan, "Hagl i juni – redusert avling")
    assert (v2.version, v2.status, v2.change_reason) == (2, "utkast", "Hagl i juni – redusert avling")
    s1v2 = _season(conn, v2, f1.id)
    assert s1v2.override_k == 5 and s1v2.override_reason == "Høy K i bladanalyse"
    assert len(repo.list_applications(conn, v2.id, "planlagt")) == 4
    assert len(repo.list_applications(conn, v2.id, "utfort")) == 1
    assert repo.list_applications(conn, plan.id, "utfort") == []
    assert len(repo.list_applications(conn, plan.id, "planlagt")) == 4  # v1 unchanged
    with pytest.raises(ValidationError, match="siste versjon"):
        ps.new_version(conn, ref, settings, plan, "igjen")


def test_copy_from_last_year(setup):
    conn, ref, settings, e, f1, f2 = setup
    p2026 = ps.create_plan(conn, ref, settings, e.id, 2026)
    s1 = _season(conn, p2026, f1.id)
    ps.save_season(conn, ref, settings, replace(s1, growth_class="gt40", override_n=3, override_reason="Fjorårets skjønn"))
    ps.apply_template(conn, ref, [_season(conn, p2026, f2.id).id], "Nyplanting")
    plan, changed = ps.copy_from_last_year(conn, ref, settings, e.id, 2027)
    assert plan.year == 2027
    assert changed == ["2 Plommer: planteår → ungtre"]
    c1 = _season(conn, plan, f1.id)
    assert c1.growth_class == "gt40"
    assert c1.override_n is None                       # overrides are year-specific
    assert c1.req_n is not None                        # recalculated
    assert len(repo.list_applications(conn, plan.id, "planlagt")) == 2
    with pytest.raises(ValidationError, match="finnes allerede"):
        ps.copy_from_last_year(conn, ref, settings, e.id, 2027)
    with pytest.raises(ValidationError, match="Fant ingen plan for 2029"):
        ps.copy_from_last_year(conn, ref, settings, e.id, 2030)


def test_copy_planned_to_actual_and_journal_totals(setup):
    conn, ref, settings, e, f1, f2 = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2026)
    s1 = _season(conn, plan, f1.id)
    ps.apply_template(conn, ref, [s1.id], "Eple bærende standard")
    planned = repo.list_applications(conn, plan.id, "planlagt")
    first_two = [a.id for a in planned[:2]]  # OPTI-NS 10 kg, patentkali 40 kg
    assert ps.copy_planned_to_actual(conn, ref, first_two, "2026-04-15", "Ola") == 2
    actual = repo.list_applications(conn, plan.id, "utfort")
    assert [(a.product_code, a.kg_per_daa, a.date, a.performed_by) for a in actual] == [
        ("opti_ns_27", 10, "2026-04-15", "Ola"), ("patentkali", 40, "2026-04-15", "Ola")]
    assert len(repo.list_applications(conn, plan.id, "planlagt")) == 4  # plan untouched

    totals = ps.build_view(conn, ref, settings, plan).journal_totals()
    field1 = next(f for f in totals["fields"] if f["row"].field_id == f1.id)
    assert field1["n_daa"] == pytest.approx(2.7)
    assert field1["k_daa"] == pytest.approx(10.0)
    assert totals["n_kg"] == pytest.approx(2.7 * 10.8)
    assert totals["p_kg"] == 0


def test_journal_date_required_and_not_in_future(setup):
    conn, ref, settings, e, f1, _ = setup
    plan = ps.create_plan(conn, ref, settings, e.id, 2026)
    s1 = _season(conn, plan, f1.id)
    with pytest.raises(ValidationError, match="Dato må fylles ut"):
        ps.save_application(conn, ref, Application(field_season_id=s1.id, kind="utfort", product_code="kalksalpeter",
                                                   kg_per_daa=10))
    with pytest.raises(ValidationError, match="fram i tid"):
        ps.save_application(conn, ref, Application(field_season_id=s1.id, kind="utfort", product_code="kalksalpeter",
                                                   kg_per_daa=10, date="2999-01-01"))
