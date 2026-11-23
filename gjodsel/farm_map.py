"""Generated farm map: property boundary and soil sample points on an open background map.

Open data only, no login or API key:
- property boundary: Kartverket Eiendom API (geokoding with omrade=true), CC BY 4.0;
- background: Kartverket Topografisk norgeskart WMS (CC BY 4.0) with NIBIO AR5 (ar5forvaltning2, NLOD 2.0).

Only fetch_map() uses the network. The result is cached per property (background image + boundary +
extent in kart.json), and points are placed at draw time, so new or moved samples show up and a PDF can
be exported again without internet. No tkinter here; Pillow is only used to merge the background layers.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import ssl
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from . import __version__
from .geo import utm_epsg, utm_zone, wgs84_to_utm

EIENDOM_URL = "https://api.kartverket.no/eiendom/v1/geokoding"
TOPO_WMS = ("https://wms.geonorge.no/skwms1/wms.topo", "topo")
GREY_WMS = ("https://wms.geonorge.no/skwms1/wms.topograatone", "topograatone")
AR5_WMS = ("https://wms.nibio.no/cgi-bin/ar5forvaltning2", "AR5flater")

BACKGROUNDS = {"ar5": "AR5 over topografisk kart (gråtone)", "topo": "Topografisk kart"}
DEFAULT_BACKGROUND = "ar5"
ATTRIBUTION = {
    "ar5": "Kart: © Kartverket (CC BY 4.0), arealtyper AR5: © NIBIO (NLOD 2.0). "
           "Eiendomsgrenser: © Kartverket (CC BY 4.0).",
    "topo": "Kart og eiendomsgrenser: © Kartverket (CC BY 4.0).",
}
BOUNDARY_NOTE = "Eiendomsgrensene er veiledende (matrikkelen); de er ikke oppmålt for denne planen."

TIMEOUT_S = 25
MAX_PIXELS = 2000  # long side of the background image (~285 dpi across an A4 page)
ASPECT = 1.25  # width / height of the map
MIN_SIZE_M = 300.0
POINT_BUFFER_M = 100.0
MAX_SIZE_M = 3000.0  # wider than this: show the area around the points only
MARGIN = 0.12
AR5_OPACITY = 0.55
MAP_FILE = "kart.json"
BACKGROUND_FILE = "bakgrunn.jpg"

Getter = Callable[[str], tuple[str, bytes]]  # url -> (content type, body)


class MapUnavailable(Exception):
    """The map could not be fetched. The message is Norwegian and meant for the user."""


@dataclass
class Teig:
    rings: list[list[list[float]]]  # [[easting, northing], ...] in the map's UTM zone; first ring is the outline
    main: bool = False  # hovedområde
    accuracy: str = ""  # Kartverket's nøyaktighetsklasse (Grønt / Gult / Rødt …)

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for ring in self.rings for p in ring]
        ys = [p[1] for ring in self.rings for p in ring]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class MapPoint:
    kind: str  # 'sample' | 'field'
    label: str
    lat: float
    lon: float


@dataclass
class FarmMap:
    """What is cached per property (kart.json) and frozen into finished plans."""
    zone: int
    extent: list[float]  # min_e, min_n, max_e, max_n (metres, EUREF89 UTM zone)
    width: int  # background image size in pixels
    height: int
    background_kind: str
    background: str  # image path relative to the data folder
    matrikkel: str
    fetched_at: str
    teiger: list[Teig] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def epsg(self) -> int:
        return utm_epsg(self.zone)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> FarmMap:
        data = dict(data)
        data["teiger"] = [Teig(**t) for t in data.get("teiger", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    # --- placing things on the map (fractions of the width/height, from the top-left corner) ---

    def fraction(self, easting: float, northing: float) -> tuple[float, float]:
        min_e, min_n, max_e, max_n = self.extent
        return (easting - min_e) / (max_e - min_e), (max_n - northing) / (max_n - min_n)

    def place(self, points: list[MapPoint]) -> tuple[list[tuple[MapPoint, float, float]], list[MapPoint]]:
        """Points inside the map with their fractions, and the points outside it."""
        inside, outside = [], []
        for p in points:
            fx, fy = self.fraction(*wgs84_to_utm(p.lat, p.lon, self.zone))
            (inside.append((p, fx, fy)) if 0 <= fx <= 1 and 0 <= fy <= 1 else outside.append(p))
        return inside, outside

    def rings(self) -> list[list[tuple[float, float]]]:
        return [[self.fraction(e, n) for e, n in ring] for t in self.teiger for ring in t.rings]

    def teiger_outside(self) -> int:
        min_e, min_n, max_e, max_n = self.extent
        return sum(1 for t in self.teiger if not _overlaps(t.bbox(), (min_e, min_n, max_e, max_n)))

    def metres_per_width(self) -> float:
        return self.extent[2] - self.extent[0]

    def fetched_date(self) -> str:
        return self.fetched_at[:10]


# --- Cache ----------------------------------------------------------------------------------------


def cache_dir(data_dir: Path, enterprise_id: int, property_id: int) -> Path:
    return data_dir / "kart" / str(enterprise_id) / f"eiendom_{property_id}"


def load_cached(data_dir: Path, enterprise_id: int, property_id: int) -> FarmMap | None:
    """The cached map of a property, or None (never fetched, or the files are damaged)."""
    folder = cache_dir(data_dir, enterprise_id, property_id)
    try:
        fm = FarmMap.from_dict(json.loads((folder / MAP_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return fm if (data_dir / fm.background).is_file() else None


def save_cache(data_dir: Path, enterprise_id: int, property_id: int, fm: FarmMap, image: bytes) -> FarmMap:
    folder = cache_dir(data_dir, enterprise_id, property_id)
    folder.mkdir(parents=True, exist_ok=True)
    background = folder / BACKGROUND_FILE
    tmp = background.with_suffix(".tmp")
    tmp.write_bytes(image)
    os.replace(tmp, background)
    fm.background = background.relative_to(data_dir).as_posix()
    tmp = folder / (MAP_FILE + ".tmp")
    tmp.write_text(json.dumps(fm.to_dict(), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / MAP_FILE)
    return fm


def freeze(fm: FarmMap, data_dir: Path, relative_target: str) -> FarmMap:
    """Copy the background to `relative_target`, so a finished plan keeps its map when the cache is refreshed."""
    target = data_dir / relative_target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(data_dir / fm.background, target)
    return FarmMap.from_dict({**fm.to_dict(), "background": relative_target})


# --- Extent ---------------------------------------------------------------------------------------


def _overlaps(a, b) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def compute_extent(points: list[tuple[float, float]], teiger: list[Teig]) -> list[float]:
    """Map area in metres: the samples and fields plus the teiger reaching into that area.

    Teiger far away (woodland, outfields) are left out, and are counted by FarmMap.teiger_outside().
    Without points, the main teig(er) are shown.
    """
    if points:
        core = _union([(e - POINT_BUFFER_M, n - POINT_BUFFER_M, e + POINT_BUFFER_M, n + POINT_BUFFER_M)
                       for e, n in points])
        box = _union([core] + [t.bbox() for t in teiger if _overlaps(t.bbox(), core)])
        if max(box[2] - box[0], box[3] - box[1]) > MAX_SIZE_M:
            box = core
    elif teiger:
        box = _union([t.bbox() for t in (([t for t in teiger if t.main]) or teiger)])
    else:
        raise MapUnavailable("Kan ikke lage kart: fant ingen eiendomsgrense, og ingen jordprøver eller skifter "
                             "på eiendommen har koordinater.")
    width, height = box[2] - box[0], box[3] - box[1]
    width, height = max(width * (1 + 2 * MARGIN), MIN_SIZE_M), max(height * (1 + 2 * MARGIN), MIN_SIZE_M)
    if width / height < ASPECT:
        width = height * ASPECT
    else:
        height = width / ASPECT
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return [round(cx - width / 2, 2), round(cy - height / 2, 2), round(cx + width / 2, 2), round(cy + height / 2, 2)]


def image_size(extent: list[float]) -> tuple[int, int]:
    width, height = extent[2] - extent[0], extent[3] - extent[1]
    if width >= height:
        return MAX_PIXELS, max(1, round(MAX_PIXELS * height / width))
    return max(1, round(MAX_PIXELS * width / height)), MAX_PIXELS


# --- Network --------------------------------------------------------------------------------------


# Python from python.org on macOS has no certificates until "Install Certificates" is run; the system
# bundle works. Windows (and the packaged exe) uses the Windows certificate store.
_SYSTEM_CA_FILES = ("/etc/ssl/cert.pem",)
_ssl_context: ssl.SSLContext | None = None


def _context() -> ssl.SSLContext:
    global _ssl_context
    if _ssl_context is None:
        context = ssl.create_default_context()
        if not context.cert_store_stats().get("x509_ca"):
            for ca_file in _SYSTEM_CA_FILES:
                if Path(ca_file).is_file():
                    context.load_verify_locations(ca_file)
                    break
        _ssl_context = context
    return _ssl_context


def http_get(url: str) -> tuple[str, bytes]:
    host = urlsplit(url).hostname or url
    request = Request(url, headers={"User-Agent": f"Gjodselplan/{__version__}"})
    try:
        with urlopen(request, timeout=TIMEOUT_S, context=_context()) as response:
            return response.headers.get("Content-Type", ""), response.read()
    except HTTPError as exc:
        raise MapUnavailable(f"{host} svarte med feil {exc.code}. Prøv igjen senere.") from exc
    except URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise MapUnavailable(f"Den sikre forbindelsen til {host} kunne ikke bekreftes (sertifikatfeil). "
                                 "Et nettverk med filter eller proxy kan gi dette.") from exc
        raise MapUnavailable(f"Fikk ikke kontakt med {host} ({exc.reason}). Kartet krever internett – sjekk "
                             "forbindelsen og prøv igjen. Resten av programmet virker som vanlig.") from exc
    except (socket.timeout, TimeoutError, ConnectionError) as exc:
        reason = getattr(exc, "reason", exc)
        raise MapUnavailable(f"Fikk ikke kontakt med {host} ({reason}). Kartet krever internett – sjekk "
                             "forbindelsen og prøv igjen. Resten av programmet virker som vanlig.") from exc


def boundary_url(kommunenr: str, gnr: int, bnr: int, fnr: int) -> str:
    return EIENDOM_URL + "?" + urlencode({
        "kommunenummer": kommunenr, "gardsnummer": gnr, "bruksnummer": bnr, "festenummer": fnr,
        "omrade": "true", "utkoordsys": 4258,
    })


def wms_url(service: tuple[str, str], zone: int, extent: list[float], width: int, height: int,
            transparent: bool = False) -> str:
    base, layer = service
    return base + "?" + urlencode({
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer, "STYLES": "",
        "CRS": f"EPSG:{utm_epsg(zone)}", "BBOX": ",".join(f"{v:.2f}" for v in extent),
        "WIDTH": width, "HEIGHT": height, "FORMAT": "image/png", "TRANSPARENT": "TRUE" if transparent else "FALSE",
    })


def parse_boundary(data: dict) -> list[tuple[list[list[list[float]]], bool, str]]:
    """Teiger from the Eiendom API GeoJSON (EPSG:4258, [lon, lat]): (rings, main, accuracy)."""
    out = []
    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        polygons = {"Polygon": [geometry.get("coordinates")], "MultiPolygon": geometry.get("coordinates")}.get(
            geometry.get("type"), [])
        for polygon in polygons or []:
            if polygon:
                out.append((polygon, bool(props.get("hovedområde")), str(props.get("nøyaktighetsklasseteig") or "")))
    return out


def _image(get: Getter, url: str, what: str) -> bytes:
    content_type, body = get(url)
    if not content_type.startswith("image/"):
        raise MapUnavailable(f"{what} svarte ikke med et kartbilde. Prøv igjen senere.")
    return body


def compose(base: bytes, overlay: bytes | None) -> bytes:
    """Merge the background layers into one JPEG."""
    from PIL import Image

    image = Image.open(io.BytesIO(base)).convert("RGBA")
    if overlay is not None:
        top = Image.open(io.BytesIO(overlay)).convert("RGBA")
        if top.size != image.size:
            top = top.resize(image.size)
        top.putalpha(top.getchannel("A").point(lambda a: round(a * AR5_OPACITY)))
        image = Image.alpha_composite(image, top)
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()


def fetch_map(kommunenr: str, gnr: int, bnr: int, fnr: int, matrikkel: str, points: list[MapPoint],
              background_kind: str = DEFAULT_BACKGROUND, get: Getter | None = None) -> tuple[FarmMap, bytes]:
    """Fetch boundary and background. Returns the map (background path not set yet) and the image.

    Raises MapUnavailable with a message for the user. Uses no database, so it can run in a thread.
    """
    get = get or http_get
    notes: list[str] = []
    try:
        raw = parse_boundary(json.loads(get(boundary_url(kommunenr, gnr, bnr, fnr))[1]))
    except ValueError as exc:
        raise MapUnavailable("Kartverket svarte med noe som ikke kunne leses. Prøv igjen senere.") from exc
    if not raw:
        notes.append(f"Fant ingen eiendomsgrense hos Kartverket for {matrikkel}. Sjekk kommunenummer, gnr. og bnr. "
                     "(flere kommunenumre ble endret 1.1.2024).")
    lons = [p.lon for p in points] or [pt[0] for rings, _main, _acc in raw for pt in rings[0]]
    if not lons:
        raise MapUnavailable("Kan ikke lage kart: fant ingen eiendomsgrense, og ingen jordprøver eller skifter "
                             "på eiendommen har koordinater.")
    zone = utm_zone(sum(lons) / len(lons))
    teiger = [Teig([[list(wgs84_to_utm(lat, lon, zone)) for lon, lat, *_ in ring] for ring in rings], main, acc)
              for rings, main, acc in raw]
    extent = compute_extent([wgs84_to_utm(p.lat, p.lon, zone) for p in points], teiger)
    width, height = image_size(extent)
    base = _image(get, wms_url(GREY_WMS if background_kind == "ar5" else TOPO_WMS, zone, extent, width, height),
                  "Kartverket")
    overlay = None
    if background_kind == "ar5":
        try:
            overlay = _image(get, wms_url(AR5_WMS, zone, extent, width, height, transparent=True), "NIBIO")
        except MapUnavailable:
            notes.append("Arealtyper (AR5) fra NIBIO kunne ikke hentes; kartet viser bare topografisk kart.")
    fm = FarmMap(zone=zone, extent=extent, width=width, height=height, background_kind=background_kind,
                 background="", matrikkel=matrikkel, fetched_at=datetime.now().isoformat(timespec="seconds"),
                 teiger=teiger, notes=notes)
    return fm, compose(base, overlay)


def nice_scale_length(metres_across: float) -> int:
    """A round scale bar length, about a fifth of the map width."""
    target = metres_across / 5
    for length in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000):
        if length >= target * 0.6:
            return length
    return 5000


def selftest_map(data_dir: Path, folder: Path) -> tuple[FarmMap, list[MapPoint]]:
    """Offline installation check: merge generated layers (Pillow, JPEG) into a map with a made-up teig."""
    from PIL import Image

    def png(colour) -> bytes:
        out = io.BytesIO()
        Image.new("RGBA", (400, 320), colour).save(out, "PNG")
        return out.getvalue()

    lat, lon, zone = 60.40, 6.70, 32
    e, n = wgs84_to_utm(lat, lon, zone)
    teig = Teig([[[e - 100, n - 80], [e + 100, n - 80], [e + 100, n + 80], [e - 100, n + 80], [e - 100, n - 80]]], True)
    folder.mkdir(parents=True, exist_ok=True)
    background = folder / "selvtest_kart.jpg"
    background.write_bytes(compose(png((235, 235, 228, 255)), png((90, 200, 90, 120))))
    fm = FarmMap(zone=zone, extent=compute_extent([(e, n)], [teig]), width=400, height=320, background_kind="ar5",
                 background=background.relative_to(data_dir).as_posix(), matrikkel="selvtest",
                 fetched_at=datetime.now().isoformat(timespec="seconds"), teiger=[teig])
    return fm, [MapPoint("sample", "Selvtestpunkt", lat, lon)]


# A fixed public spot (Rådhusplassen, Oslo) for the network check: no client or property data is sent.
_CHECK_LAT, _CHECK_LON = 59.9118, 10.7339


def network_check(get: Getter | None = None) -> list[str]:
    """Reach the three map services over HTTPS (tiny requests). Lines 'OK …' or 'FEIL …' for a log."""
    get = get or http_get
    zone = utm_zone(_CHECK_LON)
    e, n = wgs84_to_utm(_CHECK_LAT, _CHECK_LON, zone)
    extent = [e - 50, n - 40, e + 50, n + 40]
    checks = [
        ("Kartverket eiendom", EIENDOM_URL.rsplit("/", 1)[0] + "/punkt?" + urlencode(
            {"nord": _CHECK_LAT, "ost": _CHECK_LON, "koordsys": 4258, "radius": 10, "treffPerSide": 1}), "json"),
        ("Kartverket topo", wms_url(GREY_WMS, zone, extent, 50, 40), "image/"),
        ("NIBIO AR5", wms_url(AR5_WMS, zone, extent, 50, 40, transparent=True), "image/"),
    ]
    lines = []
    for name, url, expected in checks:
        try:
            content_type, body = get(url)
            ok = expected in content_type
            lines.append(f"{'OK' if ok else 'FEIL'} {name}: {content_type}, {len(body)} byte")
        except MapUnavailable as exc:
            lines.append(f"FEIL {name}: {exc}")
    return lines
