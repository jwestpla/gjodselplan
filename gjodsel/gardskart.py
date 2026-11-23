"""Georeference the aerial photo in an uploaded NIBIO gårdskart PDF (gk_enkel). Pure: no network, no tkinter.

The gårdskart PDF (Mapfish Print) has no coordinates. Its map frame holds two images on top of each other:
the aerial photo (JPEG) and a transparent overlay with the property boundary in one pink colour. The scale is
printed on the page ("Målestokk 1: 4000") and north is up, so only the position is unknown. We find it by
matching Kartverket's property boundary against the pink pixels of the overlay:

1. Hough-style voting: every (boundary sample, pink pixel) pair votes for a map position.
2. The best candidates are refined on a slightly widened pink mask (score = share of the boundary on pink).
3. The UTM zone (32/33/35) and a small scale correction are chosen by the score.

The result is confident when the score is high, enough boundary is inside the frame, and no clearly
different position scores nearly as well; otherwise the advisor aligns it by hand.
"""

from __future__ import annotations

import hashlib
import io
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .geo import utm_zone, wgs84_to_utm

BOUNDARY_RGB = (255, 153, 153)  # property boundary in the overlay ("Eiendomsgrenser")
COLOUR_TOLERANCE = 12
VOTE_BIN_PX = 3
VOTE_SAMPLES = 400
CANDIDATES = 25
SAMPLE_STEP_M = 1.0
RIVAL_DISTANCE_M = 10.0
MIN_SCORE = 0.90
MIN_MARGIN = 0.08
MIN_LENGTH_M = 150.0
SCALE_STEPS = (0.99, 0.995, 1.0, 1.005, 1.01)
POINTS_PER_METRE_PAPER = 72 / 0.0254

_MATRIKKEL_TEXT = re.compile(r"GÅRDSKART\s+(\d{4})\s*-\s*(\d+)\s*/\s*(\d+)(?:\s*/\s*(\d+))?")
_MATRIKKEL_FILE = re.compile(r"gk_[a-z]+_\d{8}_\d{2}_\d{2}-(\d{4})_(\d+)_(\d+)_(\d+)-", re.IGNORECASE)
_SCALE = re.compile(r"Målestokk\s*1\s*:\s*([\d .]+?)\s*ved", re.IGNORECASE)
_PRINTED = re.compile(r"Utskriftsdato:\s*(\d{2}\.\d{2}\.\d{4})")


class GardskartError(Exception):
    """The file can't be used as a gårdskart; the message is Norwegian, for the user."""


class GardskartMismatch(GardskartError):
    """The gårdskart belongs to another property."""


@dataclass
class GardskartPdf:
    file_name: str
    sha256: str
    matrikkel: str | None  # "4020-178/1/0"
    scale: int  # 4000 for "1: 4000"
    printed: str | None  # "15.10.2026"
    photo_jpeg: bytes
    photo_size: tuple[int, int]
    photo_rect: tuple[float, float, float, float]  # x, y, width, height in page points (y from the bottom)
    overlay: object  # PIL RGBA image
    overlay_rect: tuple[float, float, float, float]


@dataclass
class Georef:
    """Page points -> UTM metres: E = e0 + (x - left) * m_per_pt, N = n0 - (top - y) * m_per_pt."""
    zone: int
    e0: float  # UTM of the overlay's top-left corner
    n0: float
    m_per_pt: float
    left: float  # page x of the overlay's left edge
    top: float  # page y of the overlay's top edge
    score: float = 0.0  # share of the boundary on pink (±2 px)
    exact: float = 0.0  # share on a pink pixel itself
    length_m: float = 0.0  # boundary length inside the frame
    rival: float | None = None  # best score of a position more than RIVAL_DISTANCE_M away
    confident: bool = False
    method: str = "auto"  # "auto" | "manuell"

    def page_to_utm(self, x: float, y: float) -> tuple[float, float]:
        return self.e0 + (x - self.left) * self.m_per_pt, self.n0 - (self.top - y) * self.m_per_pt

    def extent(self, rect: tuple[float, float, float, float]) -> list[float]:
        x, y, w, h = rect
        e1, n1 = self.page_to_utm(x, y)
        e2, n2 = self.page_to_utm(x + w, y + h)
        return [round(min(e1, e2), 3), round(min(n1, n2), 3), round(max(e1, e2), 3), round(max(n1, n2), 3)]

    def shifted(self, d_e: float, d_n: float) -> Georef:
        return Georef(**{**asdict(self), "e0": self.e0 + d_e, "n0": self.n0 + d_n, "method": "manuell",
                         "confident": True})

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Georef:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# --- Reading the PDF ---------------------------------------------------------------------------------


def _multiply(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2, c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


def _placed_images(reader, page) -> list[tuple[object, tuple[float, float, float, float]]]:
    """Image XObjects drawn on the page with their rectangles (unrotated placements only)."""
    from pypdf.generic import ContentStream

    found = []

    def walk(content, resources, ctm, depth=0):
        if depth > 5:
            return
        xobjects = (resources or {}).get("/XObject") or {}
        stack = []
        operations = content.operations if hasattr(content, "operations") else ContentStream(content, reader).operations
        for operands, op in operations:
            if op == b"q":
                stack.append(ctm)
            elif op == b"Q":
                ctm = stack.pop() if stack else ctm
            elif op == b"cm":
                ctm = _multiply([float(v) for v in operands], ctm)
            elif op == b"Do" and operands and operands[0] in xobjects:
                xobj = xobjects[operands[0]].get_object()
                subtype = xobj.get("/Subtype")
                if subtype == "/Image":
                    a, b, c, d, e, f = ctm
                    if abs(b) < 1e-6 and abs(c) < 1e-6 and a > 0 and d > 0:
                        found.append((xobj, (e, f, a, d)))
                elif subtype == "/Form":
                    matrix = [float(v) for v in xobj.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                    walk(ContentStream(xobj, reader), xobj.get("/Resources") or resources,
                         _multiply(matrix, ctm), depth + 1)

    walk(page.get_contents(), page.get("/Resources"), (1, 0, 0, 1, 0, 0))
    return found


def _overlaps(r1, r2) -> float:
    """Share of r1's area covered by r2."""
    x1, y1, w1, h1 = r1
    x2, y2, w2, h2 = r2
    w = max(0.0, min(x1 + w1, x2 + w2) - max(x1, x2))
    h = max(0.0, min(y1 + h1, y2 + h2) - max(y1, y2))
    return (w * h) / (w1 * h1) if w1 * h1 else 0.0


def read_gardskart(path: Path) -> GardskartPdf:
    """Read photo, overlay, scale and matrikkel from a gårdskart PDF. Raises GardskartError."""
    from pypdf import PdfReader

    data = Path(path).read_bytes()
    try:
        reader = PdfReader(io.BytesIO(data))
        page = reader.pages[0]
        text = page.extract_text() or ""
        images = _placed_images(reader, page)
    except Exception as exc:  # noqa: BLE001 - any parse problem means "not usable"
        raise GardskartError(f"PDF-en kunne ikke leses ({exc}).") from exc
    scale_match = _SCALE.search(text)
    overlays = [(x, r) for x, r in images if "/SMask" in x]
    photos = [(x, r) for x, r in images if "/SMask" not in x and x.get("/Width", 0) * x.get("/Height", 0) > 250_000]
    if not scale_match or not overlays or not photos:
        raise GardskartError("Filen ser ikke ut som et gårdskart fra NIBIO (fant ikke flyfoto, kartlag og "
                             "målestokk). Last ned gårdskartet som PDF fra gardskart.nibio.no.")
    overlay_x, overlay_rect = max(overlays, key=lambda item: item[1][2] * item[1][3])
    photo_x, photo_rect = max(photos, key=lambda item: _overlaps(item[1], overlay_rect) * item[1][2] * item[1][3])
    if _overlaps(photo_rect, overlay_rect) < 0.9:
        raise GardskartError("Fant ikke flyfotoet under kartlaget i gårdskartet.")
    scale = int(re.sub(r"\D", "", scale_match.group(1)))
    found = _MATRIKKEL_TEXT.search(text) or _MATRIKKEL_FILE.search(Path(path).name)
    matrikkel = f"{found.group(1)}-{found.group(2)}/{found.group(3)}/{found.group(4) or 0}" if found else None
    printed = _PRINTED.search(text)
    try:
        overlay = overlay_x.decode_as_image().convert("RGBA")
        raw = photo_x.get_data() if photo_x.get("/Filter") == "/DCTDecode" else b""
        if not raw.startswith(b"\xff\xd8"):
            out = io.BytesIO()
            photo_x.decode_as_image().convert("RGB").save(out, "JPEG", quality=90)
            raw = out.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise GardskartError(f"Bildene i gårdskartet kunne ikke leses ({exc}).") from exc
    return GardskartPdf(file_name=Path(path).name, sha256=hashlib.sha256(data).hexdigest(), matrikkel=matrikkel,
                        scale=scale, printed=printed.group(1) if printed else None, photo_jpeg=raw,
                        photo_size=(int(photo_x["/Width"]), int(photo_x["/Height"])), photo_rect=photo_rect,
                        overlay=overlay, overlay_rect=overlay_rect)


def matrikkel_text(kommunenr: str, gnr: int, bnr: int, fnr: int) -> str:
    return f"{kommunenr}-{gnr}/{bnr}/{fnr}"


# --- Matching ------------------------------------------------------------------------------------------


def boundary_mask(overlay) -> tuple[object, list[tuple[int, int]]]:
    """Pink boundary pixels of the overlay: (mask image 'L', pixel list)."""
    from PIL import Image

    width, _height = overlay.size
    r0, g0, b0 = BOUNDARY_RGB
    tol = COLOUR_TOLERANCE
    pixels = overlay.get_flattened_data() if hasattr(overlay, "get_flattened_data") else overlay.getdata()
    pink = [(i % width, i // width) for i, (r, g, b, a) in enumerate(pixels)
            if a > 128 and abs(r - r0) <= tol and abs(g - g0) <= tol and abs(b - b0) <= tol]
    mask = Image.new("L", overlay.size, 0)
    put = mask.load()
    for x, y in pink:
        put[x, y] = 255
    return mask, pink


def _densify(ring: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    out = []
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        n = max(1, int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / step))
        out += [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n)]
    return out


def boundary_samples(teiger_lonlat: list, zone: int) -> list[tuple[float, float]]:
    """Kartverket teiger ([[lon, lat], ...] rings) as UTM points every SAMPLE_STEP_M metres."""
    return [p for rings, *_rest in teiger_lonlat for ring in rings
            for p in _densify([wgs84_to_utm(lat, lon, zone) for lon, lat, *_ in ring], SAMPLE_STEP_M)]


class _Matcher:
    def __init__(self, pdf: GardskartPdf, mask, pink):
        from PIL import ImageFilter

        self.width, self.height = pdf.overlay.size
        self.rect = pdf.overlay_rect
        self.exact = mask.load()
        self.near1 = mask.filter(ImageFilter.MaxFilter(3)).load()
        self.near = mask.filter(ImageFilter.MaxFilter(5)).load()
        self.pink = pink
        self.base_m_per_pt = pdf.scale / POINTS_PER_METRE_PAPER
        self.pt_per_px_x = self.rect[2] / self.width
        self.pt_per_px_y = self.rect[3] / self.height

    def score(self, points, e0, n0, m_per_pt) -> tuple[float, float, int, float]:
        """(share within 2 px, share on a pink pixel, samples inside the frame, graded score).

        The graded score (on the line > within 1 px > within 2 px) has a sharp top, so the fine search
        ends in the middle of the line instead of at the edge of the ±2 px plateau.
        """
        mx, my = m_per_pt * self.pt_per_px_x, m_per_pt * self.pt_per_px_y
        hit = exact = near1 = inside = 0
        width, height, near, near1_px, exact_px = self.width, self.height, self.near, self.near1, self.exact
        for e, n in points:
            px, py = (e - e0) / mx, (n0 - n) / my
            if 0 <= px < width and 0 <= py < height:
                inside += 1
                ix, iy = int(px), int(py)
                if near[ix, iy]:
                    hit += 1
                    if near1_px[ix, iy]:
                        near1 += 1
                        if exact_px[ix, iy]:
                            exact += 1
        if not inside:
            return 0.0, 0.0, 0, 0.0
        return hit / inside, exact / inside, inside, (hit + near1 + exact) / (3 * inside)

    def candidates(self, points, m_per_pt) -> list[tuple[float, float]]:
        """Top voted (e0, n0) positions of the overlay's top-left corner."""
        mx, my = m_per_pt * self.pt_per_px_x, m_per_pt * self.pt_per_px_y
        sample = points[::max(1, len(points) // VOTE_SAMPLES)]
        binned = {(x // VOTE_BIN_PX, y // VOTE_BIN_PX) for x, y in self.pink}
        size_e, size_n = VOTE_BIN_PX * mx, VOTE_BIN_PX * my
        votes: Counter = Counter()
        for e, n in sample:
            for bx, by in binned:
                votes[(int((e - (bx + 0.5) * size_e) // size_e), int((n + (by + 0.5) * size_n) // size_n))] += 1
        return [((i + 0.5) * size_e, (j + 0.5) * size_n) for (i, j), _v in votes.most_common(CANDIDATES)]

    def refine(self, points, e0, n0, m_per_pt) -> tuple[float, float, float]:
        """Local search on the graded score; returns (share within 2 px, e0, n0)."""
        mx, my = m_per_pt * self.pt_per_px_x, m_per_pt * self.pt_per_px_y
        best = (self.score(points, e0, n0, m_per_pt)[3], e0, n0)
        for step, reach in ((1.0, 3), (0.25, 4)):
            _g, be, bn = best
            for i in range(-reach, reach + 1):
                for j in range(-reach, reach + 1):
                    g = self.score(points, be + i * step * mx, bn - j * step * my, m_per_pt)[3]
                    if g > best[0]:
                        best = (g, be + i * step * mx, bn - j * step * my)
        return self.score(points, best[1], best[2], m_per_pt)[0], best[1], best[2]


def georeference(pdf: GardskartPdf, teiger_lonlat: list, zones: tuple[int, ...] | None = None) -> Georef:
    """Find where the gårdskart lies, from Kartverket's boundary ([[lon, lat]] rings per teig)."""
    mask, pink = boundary_mask(pdf.overlay)
    if len(pink) < 20:
        raise GardskartError("Fant ingen eiendomsgrense (rosa linje) i gårdskartet.")
    points_ll = [pt for rings, *_ in teiger_lonlat for ring in rings for pt in ring]
    if not points_ll:
        raise GardskartError("Kartverket har ingen eiendomsgrense for eiendommen, så flyfotoet kan ikke plasseres "
                             "automatisk.")
    lon = sum(p[0] for p in points_ll) / len(points_ll)
    zones = zones or tuple(sorted({32, 33, utm_zone(lon)}))
    matcher = _Matcher(pdf, mask, pink)
    left, top = pdf.overlay_rect[0], pdf.overlay_rect[1] + pdf.overlay_rect[3]
    best: Georef | None = None
    for zone in zones:
        points = boundary_samples(teiger_lonlat, zone)
        coarse = points[::4]  # search with every 4th sample, final fit with all
        base = matcher.base_m_per_pt
        refined = sorted((matcher.refine(coarse, e0, n0, base) for e0, n0 in matcher.candidates(points, base)),
                         reverse=True)
        score, e0, n0 = refined[0]
        # small scale correction around the best position (keeps the frame centre fixed)
        centre_x, centre_y = pdf.overlay_rect[2] / 2, pdf.overlay_rect[3] / 2
        ce, cn = e0 + centre_x * base, n0 - centre_y * base
        m_per_pt = base
        for factor in SCALE_STEPS:
            m = base * factor
            s, e_f, n_f = matcher.refine(coarse, ce - centre_x * m, cn + centre_y * m, m)
            if s > score + 1e-9:
                score, e0, n0, m_per_pt = s, e_f, n_f, m
        score, e0, n0 = matcher.refine(points, e0, n0, m_per_pt)
        _s, exact, inside, _g = matcher.score(points, e0, n0, m_per_pt)
        rivals = [s for s, e, n in refined[1:] if ((e - e0) ** 2 + (n - n0) ** 2) ** 0.5 > RIVAL_DISTANCE_M]
        rival = max(rivals) if rivals else None
        length = inside * SAMPLE_STEP_M
        confident = (score >= MIN_SCORE and length >= MIN_LENGTH_M
                     and (rival is None or score - rival >= MIN_MARGIN))
        candidate = Georef(zone=zone, e0=e0, n0=n0, m_per_pt=m_per_pt, left=left, top=top, score=round(score, 4),
                           exact=round(exact, 4), length_m=round(length, 1),
                           rival=round(rival, 4) if rival is not None else None, confident=confident)
        if best is None or (candidate.score, candidate.length_m) > (best.score, best.length_m):
            best = candidate
    return best  # type: ignore[return-value]


@dataclass
class PhotoMap:
    """What map_service needs to build the FarmMap: georeference + photo extent and source details."""
    georef: Georef
    extent: list[float]
    photo_size: tuple[int, int]
    source: dict = field(default_factory=dict)


def photo_map(pdf: GardskartPdf, georef: Georef) -> PhotoMap:
    return PhotoMap(georef=georef, extent=georef.extent(pdf.photo_rect), photo_size=pdf.photo_size, source={
        "file_name": pdf.file_name, "sha256": pdf.sha256, "scale": pdf.scale, "printed": pdf.printed,
        "matrikkel": pdf.matrikkel, "photo_rect": list(pdf.photo_rect), "overlay_rect": list(pdf.overlay_rect),
    })


def selftest(folder: Path) -> tuple[Georef, float]:
    """Offline installation check: write a small synthetic gårdskart, read it back and place it.

    Returns the georeference and the position error in metres (needs pypdf, Pillow and reportlab, no network).
    """
    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    teig = [[[6.6950, 60.3980], [6.7040, 60.3980], [6.7040, 60.4007], [6.6985, 60.4007], [6.6950, 60.3995],
             [6.6950, 60.3980]]]
    zone, scale = 32, 4000
    centre_e, centre_n = wgs84_to_utm(60.3993, 6.6990, zone)
    frame = (20.0, 206.0, 555.0, 616.0)
    size = (600, 666)
    m_per_px = scale / POINTS_PER_METRE_PAPER * frame[2] / size[0]
    e0 = centre_e - size[0] / 2 * m_per_px
    n0 = centre_n + size[1] / 2 * m_per_px
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).line([((e - e0) / m_per_px, (n0 - n) / m_per_px)
                                  for e, n in (wgs84_to_utm(lat, lon, zone) for lon, lat in teig[0])],
                                 fill=(*BOUNDARY_RGB, 255), width=3)
    folder.mkdir(parents=True, exist_ok=True)
    photo_file = folder / "selvtest_gardskart_foto.jpg"
    Image.new("RGB", size, (110, 150, 90)).save(photo_file, "JPEG")
    path = folder / "selvtest_gardskart.pdf"
    canvas = Canvas(str(path), pagesize=(595, 842))
    canvas.drawImage(str(photo_file), *frame)
    buffer = io.BytesIO()
    overlay.save(buffer, "PNG")
    canvas.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), *frame, mask="auto")
    canvas.drawString(30, 160, f"Målestokk 1: {scale} ved A4 utskrift")
    canvas.drawString(30, 120, "GÅRDSKART  4640-1/1/0")
    canvas.save()
    photo_file.unlink()
    pdf = read_gardskart(path)
    georef = georeference(pdf, [(teig, True, "")])
    x, y, w, h = pdf.overlay_rect
    found_e, found_n = georef.page_to_utm(x + w / 2, y + h / 2)
    return georef, ((found_e - centre_e) ** 2 + (found_n - centre_n) ** 2) ** 0.5
