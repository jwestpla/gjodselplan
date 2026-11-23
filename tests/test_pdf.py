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


def test_foreign_lab_report_is_appended(conn, farm, fixed_ref, settings, tmp_path):
    import json

    from pypdf import PdfReader
    from reportlab.pdfgen.canvas import Canvas

    from gjodsel.attachments import store_attachment
    from gjodsel.plan_view import PlanView

    e, p, f = farm
    lab_pdf = tmp_path / "Fertilab rapport.pdf"
    canvas = Canvas(str(lab_pdf))
    for page in range(2):
        canvas.drawString(100, 700, f"Fertilab side {page + 1}")
        canvas.showPage()
    canvas.save()
    attachment = store_attachment(tmp_path, e.id, lab_pdf)
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", origin="utenlandsk",
                                           method="annet", lab="Fertilab", attachment=attachment), [f.id])
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2024-04-01", origin="utenlandsk",
                                           method="annet", label="Borte", attachment="attachments/1/borte.pdf"), [f.id])
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    view = ps.build_view(conn, fixed_ref, settings, plan)

    without = export_plan(view, tmp_path / "uten.pdf", tmp_path / "other_data_dir")  # no reports there
    with_reports = export_plan(view, tmp_path / "med.pdf", tmp_path)
    pages_without = len(PdfReader(str(without)).pages)
    reader = PdfReader(str(with_reports))
    assert len(reader.pages) == pages_without + 2
    assert "Fertilab side 2" in reader.pages[-1].extract_text()
    text = "".join(page.extract_text() for page in reader.pages)
    assert "lagt ved bakerst i planen, 2 side(r)" in text
    assert "Borte: rapporten kunne ikke legges ved – rapportfilen mangler" in text

    # Snapshots written before origin existed still load
    data = json.loads(view.to_json())
    for sample in data["samples"]:
        del sample["origin"], sample["attachment"]
    assert PlanView.from_json(json.dumps(data)).samples[0].origin == "norsk"
