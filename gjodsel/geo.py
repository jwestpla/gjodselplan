"""Coordinates people copy out of Google Maps, and links back to it. Pure (no tkinter).

No Google API is used: the advisor finds the spot in Google Maps in the browser,
copies the coordinates (right-click → click the numbers) and pastes them here.
"""

from __future__ import annotations

import math
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


# --- WGS84 / EUREF89 <-> UTM (EPSG:258xx) -------------------------------------------------------
# Krüger series to 6th order (Karney 2011): sub-millimetre within the UTM zones used in Norway.
# WGS84 and EUREF89 (ETRS89) differ by under a metre in Norway, far below the accuracy of a
# point picked in Google Maps, so they are treated as the same here.

_A = 6378137.0
_F = 1 / 298.257222101  # GRS80
_K0 = 0.9996
_FALSE_EASTING = 500000.0
_N = _F / (2 - _F)
_E = math.sqrt(_F * (2 - _F))
_RECT = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64 + _N**6 / 256)
_ALPHA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16 + 41 * _N**4 / 180 - 127 * _N**5 / 288 + 7891 * _N**6 / 37800,
    13 * _N**2 / 48 - 3 * _N**3 / 5 + 557 * _N**4 / 1440 + 281 * _N**5 / 630 - 1983433 * _N**6 / 1935360,
    61 * _N**3 / 240 - 103 * _N**4 / 140 + 15061 * _N**5 / 26880 + 167603 * _N**6 / 181440,
    49561 * _N**4 / 161280 - 179 * _N**5 / 168 + 6601661 * _N**6 / 7257600,
    34729 * _N**5 / 80640 - 3418889 * _N**6 / 1995840,
    212378941 * _N**6 / 319334400,
)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360 - 81 * _N**5 / 512 + 96199 * _N**6 / 604800,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440 + 46 * _N**5 / 105 - 1118711 * _N**6 / 3870720,
    17 * _N**3 / 480 - 37 * _N**4 / 840 - 209 * _N**5 / 4480 + 5569 * _N**6 / 90720,
    4397 * _N**4 / 161280 - 11 * _N**5 / 504 - 830251 * _N**6 / 7257600,
    4583 * _N**5 / 161280 - 108847 * _N**6 / 3991680,
    20648693 * _N**6 / 638668800,
)


def utm_zone(lon: float) -> int:
    """UTM zone as used for Norwegian map data: 32 (south and west), 33, or 35 (east Finnmark)."""
    return 32 if lon < 12 else 33 if lon < 24 else 35


def utm_epsg(zone: int) -> int:
    return 25800 + zone


def wgs84_to_utm(lat: float, lon: float, zone: int) -> tuple[float, float]:
    """(easting, northing) in metres, EUREF89 UTM `zone` (EPSG:258<zone>)."""
    phi = math.radians(lat)
    lam = math.radians(lon - (zone * 6 - 183))
    t = math.sinh(math.atanh(math.sin(phi)) - _E * math.atanh(_E * math.sin(phi)))
    xi_ = math.atan2(t, math.cos(lam))
    eta_ = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi, eta = xi_, eta_
    for j, a in enumerate(_ALPHA, start=1):
        xi += a * math.sin(2 * j * xi_) * math.cosh(2 * j * eta_)
        eta += a * math.cos(2 * j * xi_) * math.sinh(2 * j * eta_)
    return _FALSE_EASTING + _K0 * _RECT * eta, _K0 * _RECT * xi


def utm_to_wgs84(easting: float, northing: float, zone: int) -> tuple[float, float]:
    """(lat, lon) in degrees from EUREF89 UTM `zone`."""
    xi = northing / (_K0 * _RECT)
    eta = (easting - _FALSE_EASTING) / (_K0 * _RECT)
    xi_, eta_ = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_ -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_ -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    tau_ = math.sin(xi_) / math.sqrt(math.sinh(eta_) ** 2 + math.cos(xi_) ** 2)
    tau = tau_
    for _ in range(5):  # Newton iteration for tan(latitude) from the conformal latitude
        sigma = math.sinh(_E * math.atanh(_E * tau / math.sqrt(1 + tau * tau)))
        tau_i = tau * math.sqrt(1 + sigma * sigma) - sigma * math.sqrt(1 + tau * tau)
        tau += (tau_ - tau_i) / math.sqrt(1 + tau_i * tau_i) * (1 + (1 - _E**2) * tau * tau) / (
            (1 - _E**2) * math.sqrt(1 + tau * tau))
    lam = math.atan2(math.sinh(eta_), math.cos(xi_))
    return math.degrees(math.atan(tau)), math.degrees(lam) + zone * 6 - 183
