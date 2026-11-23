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


def test_potassium_in_summaries(conn, farm, fixed_ref, settings, tmp_path):
    from pypdf import PdfReader

    e, p, f = farm
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    ps.apply_template(conn, fixed_ref, [repo.list_field_seasons(conn, plan.id)[0].id], "Eple bærende standard")
    view = ps.build_view(conn, fixed_ref, settings, plan)
    plan_text = "".join(pg.extract_text() for pg in PdfReader(str(export_plan(view, tmp_path / "p.pdf", tmp_path))).pages)
    assert "Behov K" in plan_text and "Plan K" in plan_text and "K kg/daa" in plan_text
    journal_text = PdfReader(str(export_journal(view, tmp_path / "j.pdf"))).pages[0].extract_text()
    assert "K kg/daa" in journal_text and "K kg" in journal_text


def _pdf(path, label, pages):
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(str(path))
    for page in range(pages):
        canvas.drawString(100, 700, f"{label} side {page + 1}")
        canvas.showPage()
    canvas.save()
    return path


def test_pdf_map_is_inserted_as_pages(conn, farm, fixed_ref, settings, tmp_path):
    from pypdf import PdfReader

    from gjodsel.attachments import store_attachment
    from gjodsel.models import Property

    e, p, f = farm
    second = repo.save_property(conn, Property(enterprise_id=e.id, farm_name="Leiejord", kommunenr="4642",
                                               gnr=12, bnr=4, tenure="leid"))
    repo.set_property_map(conn, p.id, store_attachment(tmp_path, e.id, _pdf(tmp_path / "kart.pdf", "Gardskart", 2)))
    broken = tmp_path / "odelagt.pdf"
    broken.write_bytes(b"ikke en pdf")
    repo.set_property_map(conn, second.id, store_attachment(tmp_path, e.id, broken))
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    view = ps.build_view(conn, fixed_ref, settings, plan)

    reader = PdfReader(str(export_plan(view, tmp_path / "plan.pdf", tmp_path)))
    texts = [page.extract_text() for page in reader.pages]
    total = len(texts)
    sketch = next(i for i, t in enumerate(texts) if t.startswith("Kartskisse"))
    assert "følger på de neste 2 sidene" in texts[sketch]
    assert "vedlagt som egen fil" not in "".join(texts)
    assert "Gardskart side 1" in texts[sketch + 1] and "Gardskart side 2" in texts[sketch + 2]
    # the next property continues after the inserted map, with the unreadable file reported
    assert "Leiejord" in texts[sketch + 3] and "KART MANGLER – kartfilen odelagt.pdf kunne ikke leses" in texts[sketch + 3]
    # footer numbering counts the inserted pages
    for number, text in enumerate(texts, start=1):
        if "Gardskart" not in text:
            assert f"Side {number} av {total}" in text


def test_sections_and_fields_start_on_own_pages(conn, farm, fixed_ref, settings, tmp_path):
    from pypdf import PdfReader

    from gjodsel.models import Field

    e, p, f = farm
    repo.save_field(conn, Field(property_id=p.id, field_no="2", name="Pærehagen", area_daa=5.0,
                                culture_code="paere", planting_year=2015))
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    view = ps.build_view(conn, fixed_ref, settings, plan)

    starts = [page.extract_text().splitlines()[0] for page in
              PdfReader(str(export_plan(view, tmp_path / "plan.pdf", tmp_path))).pages]
    assert "Jordanalyser" in starts and "Innkjøpsliste" in starts
    assert starts.index("Innkjøpsliste") == starts.index("Jordanalyser") + 1
    assert starts[-2:] == ["Gjødseljournal 2026 – skifte 1 Eplehagen", "Gjødseljournal 2026 – skifte 2 Pærehagen"]

    starts = [page.extract_text().splitlines()[0] for page in
              PdfReader(str(export_journal(view, tmp_path / "journal.pdf"))).pages]
    assert starts == ["Gjødseljournal 2026", "Skifte 2 – Pærehagen (5,0 daa, Pære)", "Sum for foretaket"]


def test_farm_report_for_standardpakke_is_appended_once(conn, farm, fixed_ref, settings, tmp_path):
    from pypdf import PdfReader

    from gjodsel.attachments import store_attachment
    from gjodsel.models import Field

    e, p, f = farm
    f2 = repo.save_field(conn, Field(property_id=p.id, field_no="2", name="Pærehagen", area_daa=5.0,
                                     culture_code="paere", planting_year=2015))
    farm_pdf = _pdf(tmp_path / "Eurofins hele garden.pdf", "Eurofins", 3)
    shared = store_attachment(tmp_path, e.id, farm_pdf)
    copy = store_attachment(tmp_path, e.id, farm_pdf)  # the same report uploaded again: identical content
    assert copy != shared
    a = repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", label="A",
                                               attachment=shared), [f.id])
    b = repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", label="B"), [f2.id])
    repo.share_soil_report(conn, [b.id], shared)
    assert repo.get_soil_sample(conn, b.id).attachment == shared
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", label="C",
                                           attachment=copy), [f2.id])
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", label="Uten rapport"), [f.id])
    (tmp_path / "bilde.png").write_bytes(b"\x89PNG ikke et ekte bilde")
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2024-04-01", label="Bilde",
                                           attachment=store_attachment(tmp_path, e.id, tmp_path / "bilde.png")), [f.id])
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    view = ps.build_view(conn, fixed_ref, settings, plan)

    plain = export_plan(view, tmp_path / "uten.pdf", tmp_path / "other_data_dir")
    reader = PdfReader(str(export_plan(view, tmp_path / "plan.pdf", tmp_path)))
    assert len(reader.pages) == len(PdfReader(str(plain)).pages) + 3  # appended once, not per sample
    assert "Eurofins side 3" in reader.pages[-1].extract_text()
    text = "".join(page.extract_text() for page in reader.pages)
    assert "prøver 01.04.2025 A, 01.04.2025 B, 01.04.2025 C: lagt ved bakerst i planen, 3 side(r)" in text
    assert "Bilde: rapporten kunne ikke legges ved – bare PDF kan legges ved planen" in text
    assert "Uten rapport: rapporten" not in text  # optional for Standardpakke 1
    total = len(reader.pages)
    assert f"Side 1 av {total}" in reader.pages[0].extract_text()  # appended pages are counted
