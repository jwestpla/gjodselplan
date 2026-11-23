"""Georeferencing the aerial photo of a gårdskart PDF. Only synthetic PDFs (made in conftest) are used."""

import pytest

from conftest import FAKE_TEIGER
from gjodsel import farm_map, gardskart, map_service, repo
from gjodsel.farm_map import MapUnavailable
from gjodsel.gardskart import GardskartError
from gjodsel.geo import wgs84_to_utm
from gjodsel.models import Property

pytest.importorskip("PIL")
pytest.importorskip("reportlab")

MAIN = FAKE_TEIGER[:2]  # main teig + the small one next to it


def _centre(lat, lon, zone=32):
    return wgs84_to_utm(lat, lon, zone)


def _recovered_centre(pdf, georef):
    x, y, w, h = pdf.overlay_rect
    return georef.page_to_utm(x + w / 2, y + h / 2)


def test_read_gardskart(tmp_path, gardskart_pdf):
    e, n = _centre(60.3994, 6.6995)
    pdf = gardskart.read_gardskart(gardskart_pdf(tmp_path / "gk_enkel_test.pdf", MAIN, e, n))
    assert (pdf.matrikkel, pdf.scale, pdf.printed) == ("4640-12/3/0", 4000, "01.04.2026")
    assert pdf.photo_size == (1185, 1314) and pdf.photo_jpeg[:2] == b"\xff\xd8"
    assert pdf.photo_rect == pytest.approx((20, 206.58, 555, 615.42), abs=0.01)
    assert pdf.overlay_rect == pytest.approx((20, 206.13, 555, 615.87), abs=0.01)
    assert pdf.overlay.mode == "RGBA" and pdf.overlay.size == (1003, 1113)
    assert len(gardskart.boundary_mask(pdf.overlay)[1]) > 1000


@pytest.mark.parametrize("scale, lat, lon", [
    (4000, 60.3992, 6.6987),  # whole teig in the frame, map not centred on it
    (1000, 60.39805, 6.6955),  # zoomed in on the south-west corner
])
def test_georeference_recovers_known_position(tmp_path, gardskart_pdf, scale, lat, lon):
    e, n = _centre(lat, lon)
    pdf = gardskart.read_gardskart(gardskart_pdf(tmp_path / "gk.pdf", MAIN, e, n, scale=scale))
    georef = gardskart.georeference(pdf, MAIN)
    found_e, found_n = _recovered_centre(pdf, georef)
    assert georef.zone == 32 and georef.confident, georef
    assert abs(found_e - e) < 1.0 and abs(found_n - n) < 1.0, (found_e - e, found_n - n)
    assert georef.score >= 0.9 and georef.length_m >= gardskart.MIN_LENGTH_M


def test_wrong_zone_scores_low(tmp_path, gardskart_pdf):
    e, n = _centre(60.3992, 6.6987)
    pdf = gardskart.read_gardskart(gardskart_pdf(tmp_path / "gk.pdf", MAIN, e, n))
    assert gardskart.georeference(pdf, MAIN, zones=(33,)).score < 0.6


def test_one_straight_edge_is_ambiguous(tmp_path, gardskart_pdf):
    # 1:1000 frame (≈196 m wide) that only shows the straight west edge: it fits anywhere along the line
    e, n = _centre(60.3994, 6.6934)
    pdf = gardskart.read_gardskart(gardskart_pdf(tmp_path / "gk.pdf", MAIN, e, n, scale=1000))
    georef = gardskart.georeference(pdf, MAIN)
    assert not georef.confident
    assert georef.rival is not None and georef.score - georef.rival < gardskart.MIN_MARGIN


def test_too_little_boundary_is_uncertain(tmp_path, gardskart_pdf):
    # only the east part of the small teig is inside the 1:1000 frame
    e, n = _centre(60.3985, 6.7071)  # the frame's west edge cuts through the small teig
    pdf = gardskart.read_gardskart(gardskart_pdf(tmp_path / "gk.pdf", MAIN[1:], e, n, scale=1000))
    georef = gardskart.georeference(pdf, MAIN[1:])
    assert not georef.confident and georef.length_m < gardskart.MIN_LENGTH_M


def test_no_boundary_in_frame(tmp_path, gardskart_pdf):
    e, n = _centre(60.3994, 6.6800)
    pdf = gardskart.read_gardskart(gardskart_pdf(tmp_path / "gk.pdf", MAIN, e, n, scale=1000))
    with pytest.raises(GardskartError, match="Fant ingen eiendomsgrense"):
        gardskart.georeference(pdf, MAIN)


def test_not_a_gardskart(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(str(tmp_path / "x.pdf"))
    canvas.drawString(100, 700, "Bare tekst")
    canvas.save()
    with pytest.raises(GardskartError, match="ser ikke ut som et gårdskart"):
        gardskart.read_gardskart(tmp_path / "x.pdf")
    (tmp_path / "y.pdf").write_bytes(b"ikke pdf")
    with pytest.raises(GardskartError, match="kunne ikke leses"):
        gardskart.read_gardskart(tmp_path / "y.pdf")


# --- Service: upload check, saving, alignment, plan --------------------------------------------------------


@pytest.fixture
def photo_farm(conn, farm, tmp_path, gardskart_pdf):
    from gjodsel.attachments import store_attachment

    e, p, f = farm
    p = repo.save_property(conn, Property(**{**p.__dict__, "kommunenr": "4640", "gnr": 12, "bnr": 3}))
    east, north = _centre(60.3992, 6.6987)
    upload = gardskart_pdf(tmp_path / "gk_enkel_20260401_10_00-4640_12_3_0-test.pdf", MAIN, east, north)
    repo.set_property_map(conn, p.id, store_attachment(tmp_path, e.id, upload))
    return e, repo.get_property(conn, p.id), f


def test_prepare_and_save_photo_map(conn, photo_farm, tmp_path, fake_map_net):
    e, p, _f = photo_farm
    attempt = map_service.prepare_photo_map(tmp_path, p, fake_map_net(teiger=MAIN))
    assert attempt.georef.confident
    fm = map_service.save_photo_map(tmp_path, p, attempt)
    assert fm.background == f"kart/{e.id}/eiendom_{p.id}/flyfoto.jpg"
    assert farm_map.active_choice(tmp_path, e.id, p.id) == farm_map.PHOTO
    loaded = farm_map.load_cached(tmp_path, e.id, p.id)
    assert loaded.background_kind == "flyfoto" and "Norge i bilder" in loaded.credit()
    assert loaded.georef["method"] == "auto" and loaded.georef["scale"] == 4000
    assert map_service.photo_is_current(tmp_path, p, loaded)
    # a generated map can live next to it; switching needs no network
    map_service.fetch(tmp_path, p, [], "ar5", fake_map_net(teiger=MAIN))
    assert farm_map.load_cached(tmp_path, e.id, p.id).background_kind == "ar5"
    farm_map.set_active(tmp_path, e.id, p.id, farm_map.PHOTO)
    assert farm_map.load_cached(tmp_path, e.id, p.id).background_kind == "flyfoto"
    farm_map.remove_photo(tmp_path, e.id, p.id)  # new gårdskart uploaded
    assert farm_map.load_cached(tmp_path, e.id, p.id).background_kind == "ar5"


def test_manual_alignment(conn, photo_farm, tmp_path, fake_map_net):
    e, p, _f = photo_farm
    fm = map_service.save_photo_map(tmp_path, p, map_service.prepare_photo_map(tmp_path, p, fake_map_net(teiger=MAIN)))
    moved = map_service.save_photo_alignment(tmp_path, p, fm, 12.5, -4.0)
    assert moved.extent[0] == pytest.approx(fm.extent[0] + 12.5) and moved.extent[1] == pytest.approx(fm.extent[1] - 4)
    loaded = farm_map.load_cached(tmp_path, e.id, p.id)
    assert loaded.georef["method"] == "manuell" and loaded.notes == ["Flyfotoet er plassert manuelt."]
    assert loaded.georef["e0"] == pytest.approx(fm.georef["e0"] + 12.5)


def test_upload_checks(conn, photo_farm, tmp_path, fake_map_net, gardskart_pdf):
    from gjodsel.attachments import store_attachment

    e, p, _f = photo_farm
    with pytest.raises(MapUnavailable, match="internett"):
        map_service.prepare_photo_map(tmp_path, p, fake_map_net(offline=True))
    other = gardskart_pdf(tmp_path / "annen.pdf", MAIN, *_centre(60.3992, 6.6987), matrikkel="4640-99/1/0")
    repo.set_property_map(conn, p.id, store_attachment(tmp_path, e.id, other))
    with pytest.raises(GardskartError, match="gjelder 4640-99/1/0, men eiendommen er 4640-12/3/0"):
        map_service.uploaded_gardskart(tmp_path, repo.get_property(conn, p.id))
    repo.set_property_map(conn, p.id, None)
    with pytest.raises(GardskartError, match="Last opp gårdskartet"):
        map_service.uploaded_gardskart(tmp_path, repo.get_property(conn, p.id))


def test_photo_map_in_plan_pdf_and_frozen(conn, photo_farm, fixed_ref, settings, tmp_path, fake_map_net):
    from pypdf import PdfReader

    from gjodsel import plan_service as ps
    from gjodsel.geo import utm_to_wgs84
    from gjodsel.models import Field, SoilSample
    from gjodsel.pdf_export import export_plan

    e, p, f = photo_farm
    fm = map_service.save_photo_map(tmp_path, p, map_service.prepare_photo_map(tmp_path, p, fake_map_net(teiger=MAIN)))
    lat, lon = utm_to_wgs84((fm.extent[0] + fm.extent[2]) / 2, (fm.extent[1] + fm.extent[3]) / 2, fm.zone)
    f = repo.save_field(conn, Field(**{**f.__dict__, "lat": lat, "lon": lon}))
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", label="Prøve A",
                                           lat=lat, lon=lon), [f.id])
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    view = ps.build_view(conn, fixed_ref, settings, plan, data_dir=tmp_path)
    text = "".join(pg.extract_text() for pg in PdfReader(str(export_plan(view, tmp_path / "p.pdf", tmp_path))).pages)
    assert "Prøve A" in text and "fra NIBIO Gårdskart utskrevet 01.04.2026" in text
    finished = ps.finalize(conn, fixed_ref, settings, plan, data_dir=tmp_path)
    frozen = ps.build_view(conn, fixed_ref, settings, finished, data_dir=tmp_path).properties[0]["generated_map"]
    assert frozen["background_kind"] == "flyfoto" and (tmp_path / frozen["background"]).is_file()


def test_selftest_places_synthetic_gardskart(tmp_path):
    georef, error = gardskart.selftest(tmp_path)
    assert georef.confident and error < 1.0
