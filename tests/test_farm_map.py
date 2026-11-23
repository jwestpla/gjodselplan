import json

import pytest

from gjodsel import farm_map, map_service, repo
from gjodsel.farm_map import FarmMap, MapPoint, MapUnavailable, Teig
from gjodsel.geo import utm_to_wgs84, utm_zone, wgs84_to_utm
from gjodsel.models import Field, Property, SoilSample

pytest.importorskip("PIL")

POINTS = [MapPoint("sample", "Prøve A", 60.3990, 6.6980), MapPoint("field", "Skifte 1", 60.3995, 6.7010)]


# --- WGS84 <-> UTM -------------------------------------------------------------------------------

# Reference values from Kartverket's transformation service (EPSG:4258 -> 25832/25833/25835).
@pytest.mark.parametrize("lat, lon, zone, easting, northing", [
    (61.099, 7.483, 32, 418212.3551576163, 6774762.465642417),
    (61.099, 7.483, 33, 95324.2022598898, 6797103.440663882),
    (59.91, 10.75, 32, 597868.3810645777, 6642681.510038425),
    (69.65, 18.95, 33, 653210.0894847008, 7731796.8302624365),
    (70.07, 29.7, 35, 602687.484399863, 7775954.51488019),
    (60.39, 5.32, 32, 297230.2202071025, 6700510.175130839),
])
def test_utm_matches_kartverket(lat, lon, zone, easting, northing):
    e, n = wgs84_to_utm(lat, lon, zone)
    assert e == pytest.approx(easting, abs=0.001) and n == pytest.approx(northing, abs=0.001)
    back = utm_to_wgs84(e, n, zone)
    assert back[0] == pytest.approx(lat, abs=1e-9) and back[1] == pytest.approx(lon, abs=1e-9)


def test_utm_zone_for_norway():
    assert [utm_zone(lon) for lon in (5.3, 10.7, 12.0, 18.9, 29.7)] == [32, 32, 33, 33, 35]


# --- Boundary, extent, URLs --------------------------------------------------------------------------


def _teig(min_e, min_n, max_e, max_n, main=False):
    return Teig([[[min_e, min_n], [max_e, min_n], [max_e, max_n], [min_e, max_n], [min_e, min_n]]], main)


def test_parse_boundary_polygon_and_multipolygon():
    data = {"features": [
        {"geometry": {"type": "Polygon", "coordinates": [[[6.7, 60.4], [6.71, 60.4], [6.7, 60.41]]]},
         "properties": {"hovedområde": True, "nøyaktighetsklasseteig": "Gult"}},
        {"geometry": {"type": "MultiPolygon", "coordinates": [[[[1, 2], [3, 4], [5, 6]]], [[[7, 8], [9, 1], [2, 3]]]]},
         "properties": {}},
        {"geometry": None, "properties": {}},
    ]}
    teiger = farm_map.parse_boundary(data)
    assert len(teiger) == 3 and teiger[0][1:] == (True, "Gult") and teiger[1][1:] == (False, "")
    assert farm_map.parse_boundary({"features": []}) == []


def test_extent_around_points_with_nearby_teig_and_aspect():
    points = [(1000.0, 5000.0), (1100.0, 5050.0)]
    near = _teig(900, 4900, 1500, 5100, main=True)
    far = _teig(9000, 9000, 9100, 9100)
    extent = farm_map.compute_extent(points, [near, far])
    width, height = extent[2] - extent[0], extent[3] - extent[1]
    assert width / height == pytest.approx(farm_map.ASPECT)
    assert extent[0] < 900 and extent[2] > 1500  # the nearby teig is shown whole
    fm = FarmMap(zone=32, extent=extent, width=10, height=8, background_kind="ar5", background="", matrikkel="",
                 fetched_at="", teiger=[near, far])
    assert fm.teiger_outside() == 1


def test_extent_minimum_size_and_far_teig_fallback():
    extent = farm_map.compute_extent([(1000.0, 5000.0)], [])
    assert extent[2] - extent[0] >= farm_map.MIN_SIZE_M and extent[3] - extent[1] >= farm_map.MIN_SIZE_M
    huge = _teig(0, 0, 20000, 20000)  # a big woodland teig touching the point: don't zoom out to it
    extent = farm_map.compute_extent([(1000.0, 5000.0)], [huge])
    assert extent[2] - extent[0] < 1000


def test_extent_without_points_uses_main_teig():
    extent = farm_map.compute_extent([], [_teig(0, 0, 400, 300, main=True), _teig(9000, 9000, 9100, 9100)])
    assert extent[0] < 0 and extent[2] > 400 and extent[2] < 1000
    with pytest.raises(MapUnavailable, match="ingen eiendomsgrense"):
        farm_map.compute_extent([], [])


def test_urls():
    assert farm_map.boundary_url("4640", 12, 3, 0) == (
        "https://api.kartverket.no/eiendom/v1/geokoding?kommunenummer=4640&gardsnummer=12&bruksnummer=3"
        "&festenummer=0&omrade=true&utkoordsys=4258")
    url = farm_map.wms_url(farm_map.AR5_WMS, 32, [1.5, 2, 3, 4], 2000, 1600, transparent=True)
    assert "LAYERS=AR5flater" in url and "CRS=EPSG%3A25832" in url and "BBOX=1.50%2C2.00%2C3.00%2C4.00" in url
    assert "TRANSPARENT=TRUE" in url and url.startswith("https://wms.nibio.no/cgi-bin/ar5forvaltning2?")
    assert farm_map.image_size([0, 0, 1250, 1000]) == (2000, 1600)
    assert farm_map.image_size([0, 0, 1000, 1250]) == (1600, 2000)


# --- Fetching ----------------------------------------------------------------------------------


def test_fetch_map(fake_map_net):
    net = fake_map_net()
    fm, image = farm_map.fetch_map("4640", 12, 3, 0, "4640-12/3", POINTS, "ar5", net)
    assert len(net.calls) == 3 and "wms.topograatone" in net.calls[1] and "ar5forvaltning2" in net.calls[2]
    assert fm.zone == 32 and len(fm.teiger) == 3 and fm.notes == []
    assert image[:2] == b"\xff\xd8"  # JPEG
    inside, outside = fm.place(POINTS)
    assert [p.label for p, _x, _y in inside] == ["Prøve A", "Skifte 1"] and outside == []
    assert fm.teiger_outside() == 1  # the woodland teig 6 km away
    assert all(0 <= fx <= 1 and 0 <= fy <= 1 for fx, fy in fm.rings()[0])  # the main teig is shown whole

    topo = farm_map.fetch_map("4640", 12, 3, 0, "4640-12/3", POINTS, "topo", fake_map_net())[0]
    assert topo.background_kind == "topo"


def test_fetch_map_partial_and_failures(fake_map_net):
    fm, _image = farm_map.fetch_map("4640", 12, 3, 0, "4640-12/3", POINTS, "ar5", fake_map_net(ar5_ok=False))
    assert "AR5" in fm.notes[0]
    fm, _image = farm_map.fetch_map("9999", 1, 1, 0, "9999-1/1", POINTS, "ar5", fake_map_net(teiger=[]))
    assert fm.teiger == [] and "Fant ingen eiendomsgrense" in fm.notes[0]
    with pytest.raises(MapUnavailable, match="ingen eiendomsgrense"):
        farm_map.fetch_map("9999", 1, 1, 0, "9999-1/1", [], "ar5", fake_map_net(teiger=[]))
    with pytest.raises(MapUnavailable, match="kartbilde"):
        farm_map.fetch_map("4640", 12, 3, 0, "4640-12/3", POINTS, "ar5", fake_map_net(topo_ok=False))
    with pytest.raises(MapUnavailable, match="internett"):
        farm_map.fetch_map("4640", 12, 3, 0, "4640-12/3", POINTS, "ar5", fake_map_net(offline=True))


def test_http_get_reports_no_connection(monkeypatch):
    from urllib.error import URLError

    def no_network(*_args, **_kwargs):
        raise URLError("getaddrinfo failed")

    monkeypatch.setattr(farm_map, "urlopen", no_network)
    with pytest.raises(MapUnavailable, match="Fikk ikke kontakt med api.kartverket.no.*Resten av programmet"):
        farm_map.http_get(farm_map.boundary_url("4640", 1, 1, 0))


# --- Cache and service -------------------------------------------------------------------------------


@pytest.fixture
def farm_with_points(conn, farm):
    e, p, f = farm
    f = repo.save_field(conn, Field(**{**f.__dict__, "lat": 60.3995, "lon": 6.7010}))
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", label="Prøve A",
                                           lat=60.3990, lon=6.6980), [f.id])
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", label="Uten punkt"), [f.id])
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2025-04-01", label="Ikke knyttet",
                                           lat=60.0, lon=6.0), [])
    return e, p, f


def test_property_points(conn, farm_with_points):
    e, p, _f = farm_with_points
    points, missing = map_service.property_points(conn, e.id, p.id)
    assert [(x.kind, x.label) for x in points] == [("sample", "Prøve A"), ("field", "Skifte 1")]
    assert missing == ["Uten punkt"]
    other = repo.save_property(conn, Property(enterprise_id=e.id, kommunenr="4640", gnr=99, bnr=1))
    assert map_service.property_points(conn, e.id, other.id) == ([], [])


def test_cache_roundtrip_and_freeze(conn, farm_with_points, tmp_path, fake_map_net):
    e, p, _f = farm_with_points
    points, _missing = map_service.property_points(conn, e.id, p.id)
    assert farm_map.load_cached(tmp_path, e.id, p.id) is None
    fm = map_service.fetch(tmp_path, p, points, "ar5", fake_map_net())
    assert fm.background == f"kart/{e.id}/eiendom_{p.id}/bakgrunn.jpg"
    loaded = farm_map.load_cached(tmp_path, e.id, p.id)
    assert loaded == fm
    frozen = farm_map.freeze(loaded, tmp_path, "attachments/1/planer/x.jpg")
    assert (tmp_path / frozen.background).read_bytes() == (tmp_path / fm.background).read_bytes()
    (tmp_path / fm.background).unlink()
    assert farm_map.load_cached(tmp_path, e.id, p.id) is None  # image gone: treated as not fetched
    (tmp_path / fm.background).write_bytes(b"x")
    (tmp_path / "kart" / str(e.id) / f"eiendom_{p.id}" / farm_map.MAP_FILE).write_text("{ødelagt", encoding="utf-8")
    assert farm_map.load_cached(tmp_path, e.id, p.id) is None


def test_view_data_is_json(conn, farm_with_points, tmp_path, fake_map_net):
    e, p, _f = farm_with_points
    fm = map_service.fetch(tmp_path, p, map_service.property_points(conn, e.id, p.id)[0], "ar5", fake_map_net())
    data = json.loads(json.dumps(map_service.view_data(conn, e.id, p.id, fm)))
    again, points, missing = map_service.from_view_data(data)
    assert again == fm and len(points) == 2 and missing == ["Uten punkt"]


# --- Plan and PDF ------------------------------------------------------------------------------------


def test_plan_uses_cached_map_and_freezes_it(conn, farm_with_points, fixed_ref, settings, tmp_path, fake_map_net):
    from gjodsel import plan_service as ps

    e, p, _f = farm_with_points
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    view = ps.build_view(conn, fixed_ref, settings, plan, data_dir=tmp_path)
    assert view.properties[0]["generated_map"] is None
    assert any("Kartskisse mangler" in w for w in view.warnings)

    map_service.fetch(tmp_path, p, map_service.property_points(conn, e.id, p.id)[0], "ar5", fake_map_net())
    view = ps.build_view(conn, fixed_ref, settings, plan, data_dir=tmp_path)
    assert view.properties[0]["generated_map"]["points"][0]["label"] == "Prøve A"
    assert not any("Kartskisse mangler" in w for w in view.warnings)  # the generated map counts as a map
    assert ps.build_view(conn, fixed_ref, settings, plan).properties[0]["generated_map"] is None  # no data_dir

    finished = ps.finalize(conn, fixed_ref, settings, plan, data_dir=tmp_path)
    frozen = ps.build_view(conn, fixed_ref, settings, finished, data_dir=tmp_path).properties[0]["generated_map"]
    assert frozen["background"] == map_service.frozen_path(e.id, plan.id, p.id)
    assert (tmp_path / frozen["background"]).is_file()
    # A later sample does not change the finished plan's map
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2026-05-01", label="Ny prøve",
                                           lat=60.3992, lon=6.6990), [_f.id])
    again = ps.build_view(conn, fixed_ref, settings, finished, data_dir=tmp_path).properties[0]["generated_map"]
    assert [pt["label"] for pt in again["points"]] == ["Prøve A", "Skifte 1"]


def test_pdf_has_generated_map_then_uploaded_map(conn, farm_with_points, fixed_ref, settings, tmp_path, fake_map_net):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader
    from reportlab.pdfgen.canvas import Canvas

    from gjodsel import plan_service as ps
    from gjodsel.attachments import store_attachment
    from gjodsel.pdf_export import export_plan

    e, p, f = farm_with_points
    nibio = tmp_path / "gk_enkel.pdf"
    canvas = Canvas(str(nibio))
    canvas.drawString(100, 700, "Generert gardskart side 1")
    canvas.save()
    repo.set_property_map(conn, p.id, store_attachment(tmp_path, e.id, nibio))
    map_service.fetch(tmp_path, p, map_service.property_points(conn, e.id, p.id)[0], "ar5", fake_map_net())
    # A sample added after the map was fetched, far outside it
    repo.save_soil_sample(conn, SoilSample(enterprise_id=e.id, sample_date="2026-05-01", label="Langt unna",
                                           lat=60.50, lon=6.90), [f.id])
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    view = ps.build_view(conn, fixed_ref, settings, plan, data_dir=tmp_path)

    reader = PdfReader(str(export_plan(view, tmp_path / "plan.pdf", tmp_path)))
    texts = [page.extract_text() for page in reader.pages]
    page = next(i for i, t in enumerate(texts) if t.startswith("Kartskisse"))
    text = texts[page]
    for expected in ("Eiendomskart med jordprøvepunkter", "Prøve A", "Skifte 1", "© NIBIO (NLOD 2.0)",
                     "Utenfor kartutsnittet (hent kartet på nytt): Langt unna", "Jordprøver uten koordinater",
                     "Uten punkt", "1 teig av eiendommen ligger utenfor kartutsnittet",
                     "Opplastet gårdskart (gk_enkel.pdf) følger på neste side", "60,399° N, 6,698° Ø"):
        assert expected in text, expected
    assert "KART MANGLER" not in "".join(texts)
    assert "Generert gardskart side 1" in texts[page + 1]
    assert f"Side {page + 2} av {len(texts)}" not in texts[page + 1]  # the inserted page keeps its own content
    assert f"Side {page + 3} av {len(texts)}" in texts[page + 2]


def test_pdf_without_any_map_still_warns(conn, farm, fixed_ref, settings, tmp_path):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader

    from gjodsel import plan_service as ps
    from gjodsel.pdf_export import export_plan

    e, _p, _f = farm
    plan = ps.create_plan(conn, fixed_ref, settings, e.id, 2026)
    view = ps.build_view(conn, fixed_ref, settings, plan, data_dir=tmp_path)
    text = "".join(pg.extract_text() for pg in PdfReader(str(export_plan(view, tmp_path / "p.pdf", tmp_path))).pages)
    assert "KART MANGLER – påkrevd etter § 26" in text


def test_network_check(fake_map_net):
    lines = farm_map.network_check(fake_map_net())
    assert [line.split(":")[0] for line in lines] == ["OK Kartverket eiendom", "OK Kartverket topo", "OK NIBIO AR5"]
    assert all(line.startswith("FEIL") for line in farm_map.network_check(fake_map_net(offline=True)))
    assert farm_map.network_check(fake_map_net(ar5_ok=False))[2].startswith("FEIL NIBIO AR5")


def test_selftest_map_renders_offline(tmp_path):
    pytest.importorskip("reportlab")
    from gjodsel.pdf_export import _generated_map

    fm, points = farm_map.selftest_map(tmp_path, tmp_path / "export")
    assert (tmp_path / fm.background).is_file() and fm.place(points)[1] == []
    flowables = _generated_map({**fm.to_dict(), "points": [p.__dict__ for p in points], "missing_coordinates": []},
                               tmp_path)
    assert any(type(f).__name__ == "_FarmMapFlowable" for f in flowables)
