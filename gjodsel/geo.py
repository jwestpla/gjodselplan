"""Coordinates people copy out of Google Maps, and links back to it. Pure (no tkinter).

No Google API is used: the advisor finds the spot in Google Maps in the browser,
copies the coordinates (right-click → click the numbers) and pastes them here.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from .units import format_plain, parse_number

_DOT = r"-?\d{1,3}(?:\.\d+)?"
# Google Maps links: "!3d<lat>!4d<lon>" is the exact place, "@<lat>,<lon>,<zoom>z" the map centre.
_LINK_PLACE = re.compile(rf"!3d({_DOT})!4d({_DOT})")
_LINK_AT = re.compile(rf"@({_DOT}),({_DOT})")
_LINK_QUERY = re.compile(rf"[?&](?:q|query|ll|center|destination)=({_DOT})\s*,\s*({_DOT})", re.IGNORECASE)
# Plain pairs. A bare comma only separates when both numbers have a decimal point, so a single
# value with decimal comma ("61,123") is never read as a pair.
_NUM = r"[-+]?\d{1,3}(?:[.,]\d+)?"
_PAIR_SPACED = re.compile(rf"^\(?\s*({_NUM})\s*(?:;|,\s+|\s+)\s*({_NUM})\s*\)?$")
_PAIR_TIGHT = re.compile(r"^\(?\s*([-+]?\d{1,3}\.\d+),([-+]?\d{1,3}\.\d+)\s*\)?$")
# Degrees (decimal, or with minutes/seconds) plus hemisphere: 61°06'24.2"N 7°28'55.6"E, 61.1° N 7.4° Ø
_DMS_PART = (r"(\d{1,3}(?:[.,]\d+)?)\s*°\s*(?:(\d{1,2}(?:[.,]\d+)?)\s*['′’]\s*)?"
             r"(?:(\d{1,2}(?:[.,]\d+)?)\s*(?:\"|″|”|''|’’)\s*)?([NSØEWV])")
_DMS = re.compile(rf"{_DMS_PART}[\s,;]*{_DMS_PART}", re.IGNORECASE)
_SHORT_LINK = re.compile(r"(maps\.app\.goo\.gl|goo\.gl/maps)", re.IGNORECASE)

# Loose box around mainland Norway, only used for a warning.
_NORWAY = (57.5, 71.5, 4.0, 31.5)


def parse_coordinates(text: str | None) -> tuple[float, float] | None:
    """(latitude, longitude) from a Google Maps copy, a Google Maps link or a DMS string; None if not found."""
    raw = (text or "").strip()
    if not raw:
        return None
    link = unquote(raw)
    for pattern in (_LINK_PLACE, _LINK_AT, _LINK_QUERY):
        m = pattern.search(link)
        if m:
            return _valid(float(m.group(1)), float(m.group(2)))
    m = _DMS.search(raw)
    if m:
        return _from_dms(m.groups())
    for pattern in (_PAIR_SPACED, _PAIR_TIGHT):
        m = pattern.match(raw)
        if m:
            try:
                return _valid(parse_number(m.group(1)), parse_number(m.group(2)))
            except ValueError:
                return None
    return None


def is_short_link(text: str | None) -> bool:
    """Google's shortened share links can't be read without going online."""
    return bool(_SHORT_LINK.search(text or ""))


def google_maps_url(lat: float, lon: float) -> str:
    """Satellite view centred on the point (official Maps URL, no API key)."""
    return (f"https://www.google.com/maps/@?api=1&map_action=map&center={lat:.6f},{lon:.6f}"
            "&zoom=18&basemap=satellite")


def google_maps_search_url(query: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote(query, safe='')}"


def outside_norway(lat: float, lon: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = _NORWAY
    return not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max)


def format_coordinates(lat: float, lon: float) -> str:
    """'61,12345° N, 7,48211° Ø'."""
    ns = "N" if lat >= 0 else "S"
    ew = "Ø" if lon >= 0 else "V"
    return f"{format_plain(round(abs(lat), 6))}° {ns}, {format_plain(round(abs(lon), 6))}° {ew}"


def _valid(lat: float | None, lon: float | None) -> tuple[float, float] | None:
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return lat, lon


def _from_dms(groups: tuple) -> tuple[float, float] | None:
    lat = lon = None
    for deg, minutes, seconds, hemisphere in (groups[:4], groups[4:]):
        value = parse_number(deg) + (parse_number(minutes) or 0) / 60 + (parse_number(seconds) or 0) / 3600
        h = hemisphere.upper()
        if h in "NS":
            lat = -value if h == "S" else value
        else:
            lon = -value if h in "WV" else value
    return _valid(lat, lon)
