import pytest

from gjodsel.geo import (
    format_coordinates,
    google_maps_search_url,
    google_maps_url,
    is_short_link,
    outside_norway,
    parse_coordinates,
)


def close(result, lat, lon):
    return result is not None and result[0] == pytest.approx(lat, abs=1e-6) and result[1] == pytest.approx(lon, abs=1e-6)


@pytest.mark.parametrize("text", [
    "61.12345, 7.48211",            # Google Maps right-click copy
    "  61.12345, 7.48211\n",
    "61.12345,7.48211",
    "61,12345; 7,48211",            # decimal comma
    "61,12345, 7,48211",
    "61,12345 7,48211",
    "(61.12345, 7.48211)",
])
def test_plain_pairs(text):
    assert close(parse_coordinates(text), 61.12345, 7.48211)


@pytest.mark.parametrize("text, lat, lon", [
    ("https://www.google.com/maps/@61.1234567,7.4821111,17z", 61.1234567, 7.4821111),
    ("https://www.google.com/maps/@61.1234567,7.4821111,450m/data=!3m1!1e3", 61.1234567, 7.4821111),
    # The place marker (!3d!4d) wins over the map centre (@)
    ("https://www.google.com/maps/place/Lærdal/@61.09,7.40,13z/data=!4m6!3m5!1s0x0:0x0!8m2!3d61.1001!4d7.4802",
     61.1001, 7.4802),
    ("https://www.google.com/maps?q=61.12345,7.48211", 61.12345, 7.48211),
    ("https://www.google.com/maps/search/?api=1&query=61.12345%2C7.48211", 61.12345, 7.48211),
    ("https://maps.google.com/?ll=-33.712206,150.311941", -33.712206, 150.311941),
])
def test_google_links(text, lat, lon):
    assert close(parse_coordinates(text), lat, lon)


@pytest.mark.parametrize("text, lat, lon", [
    ("61°06'24.2\"N 7°28'55.6\"E", 61 + 6 / 60 + 24.2 / 3600, 7 + 28 / 60 + 55.6 / 3600),
    ("61°06′24,2″N 7°28′55,6″Ø", 61 + 6 / 60 + 24.2 / 3600, 7 + 28 / 60 + 55.6 / 3600),
    ("61.12345° N, 7.48211° E", 61.12345, 7.48211),
    ("7°28'55.6\"E 61°06'24.2\"N", 61 + 6 / 60 + 24.2 / 3600, 7 + 28 / 60 + 55.6 / 3600),  # order by letter
    ("33°42'43.9\"S 150°18'43.0\"W", -(33 + 42 / 60 + 43.9 / 3600), -(150 + 18 / 60 + 43.0 / 3600)),
])
def test_degrees_minutes_seconds(text, lat, lon):
    assert close(parse_coordinates(text), lat, lon)


@pytest.mark.parametrize("text", [
    "", None, "Eplehagen", "61,123",          # a single value with decimal comma is not a pair
    "61.12345", "91.0, 7.0", "61.0, 181.0",   # out of range
    "https://maps.app.goo.gl/AbCdEf123",      # short link, can't be read offline
    "Skifte 12 e 5 v",                        # hemisphere letters need a degree sign
])
def test_not_coordinates(text):
    assert parse_coordinates(text) is None


def test_short_link_detection():
    assert is_short_link("https://maps.app.goo.gl/AbCdEf123")
    assert not is_short_link("61.1, 7.4")


def test_urls_use_dot_decimals():
    assert google_maps_url(61.1, 7.48) == (
        "https://www.google.com/maps/@?api=1&map_action=map&center=61.100000,7.480000&zoom=18&basemap=satellite")
    assert google_maps_search_url("Testvegen 1, 6887 Lærdal") == (
        "https://www.google.com/maps/search/?api=1&query=Testvegen%201%2C%206887%20L%C3%A6rdal")


def test_norway_box_and_format():
    assert not outside_norway(61.1, 7.48)
    assert outside_norway(7.48, 61.1)  # swapped
    assert format_coordinates(61.12345, 7.48211) == "61,12345° N, 7,48211° Ø"
    assert format_coordinates(-33.5, -150.25) == "33,5° S, 150,25° V"
