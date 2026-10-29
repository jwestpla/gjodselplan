import pytest

pytest.importorskip("reportlab")

from gjodsel import plan_service as ps  # noqa: E402
from gjodsel import repo  # noqa: E402
from gjodsel.models import Application, SoilSample  # noqa: E402
from gjodsel.pdf_export import default_filename, export_journal, export_plan, pdf_text, plain_text  # noqa: E402


def test_text_mapping():
    assert plain_text("K-HNO₃ −25 % ≥15,5 → ⚠") == "K-HNO3 -25 % >=15,5 -> !"
    assert pdf_text("Frukt & Bær <tunnel>") == "Frukt &amp; Bær &lt;tunnel&gt;"
    assert plain_text("æøå ÆØÅ § – · ×") == "æøå ÆØÅ § – · ×"


def test_export_plan_and_journal(conn, farm, fixed_ref, settings, tmp_path):
    e, p, f = farm
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", p_al=24.9, k_al=10, ph=5.2),
                          [f.id])
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    season = repo.list_field_seasons(conn, plan.id)[0]
    ps.apply_template(conn, fixed_ref, [season.id], "Eple bærende standard")
    ps.save_application(conn, fixed_ref, Application(field_season_id=season.id, kind="utfort",
                                                     product_code="kalksalpeter", kg_per_daa=10, date="2026-05-01"))
    plan = ps.finalize(conn, fixed_ref, settings, plan)
    view = ps.build_view(conn, fixed_ref, settings, plan)

    plan_pdf = export_plan(view, tmp_path / "plan.pdf", tmp_path)  # no map file -> placeholder, no crash
    journal_pdf = export_journal(view, tmp_path / "journal.pdf")
    for path in (plan_pdf, journal_pdf):
        assert path.read_bytes().startswith(b"%PDF")
        assert path.stat().st_size > 1500
    assert default_filename(view, "Gjodslingsplan") == "Gjodslingsplan_Testgård_2026_v1.pdf"
