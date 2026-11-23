"""PDF export (PLAN §8): the full fertilizer plan and the § 27 fertilizer journal.

Renders a PlanView only, so a finished plan prints exactly as frozen.
Uses reportlab's built-in Helvetica (no font files needed); characters it
cannot encode (subscripts, Unicode minus, ≥, …) are mapped to plain text.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import farm_map, map_service
from .geo import format_coordinates
from .plan_view import FieldRow, PlanView, SampleLine
from .units import format_date, format_number, format_plain

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
HAND_ROWS = 15  # empty rows on each field's journal-form page, for hand entries

_REPLACE = {
    "₂": "2", "₃": "3", "₅": "5", "−": "-", "≥": ">=", "≤": "<=", "→": "->", "⚠": "!", "▼": "(lav)",
    "▲": "(høy)", "✓": "OK", " ": " ", " ": " ",
}

_styles = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_styles["Heading1"], fontSize=16, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=_styles["Heading2"], fontSize=12.5, spaceBefore=8, spaceAfter=4)
H3 = ParagraphStyle("h3", parent=_styles["Heading3"], fontSize=10.5, spaceBefore=6, spaceAfter=3)
BODY = ParagraphStyle("body", parent=_styles["Normal"], fontSize=8.8, leading=11.2)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.4, leading=9.2)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=7.8, leading=9.4)
CELL_R = ParagraphStyle("cell_r", parent=CELL, alignment=TA_RIGHT)
WARN = ParagraphStyle("warn", parent=BODY, textColor=colors.HexColor("#b00020"))
BIG_WARN = ParagraphStyle("big_warn", parent=BODY, fontSize=12, leading=15, textColor=colors.HexColor("#b00020"))

_GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ("TOPPADDING", (0, 0), (-1, -1), 1.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
])


def plain_text(text) -> str:
    """Map characters Helvetica can't encode (for canvas drawing, no markup)."""
    s = str(text if text is not None else "")
    for k, v in _REPLACE.items():
        s = s.replace(k, v)
    return s.encode("cp1252", errors="replace").decode("cp1252")


def pdf_text(text) -> str:
    """plain_text, escaped for reportlab's Paragraph mini-markup."""
    return escape(plain_text(text))


def P(text, style=BODY, bold: bool = False) -> Paragraph:
    s = pdf_text(text).replace("\n", "<br/>")
    return Paragraph(f"<b>{s}</b>" if bold else s, style)


def _num(value, decimals: int = 1, empty: str = "–") -> str:
    return format_number(value, decimals, empty)


def _table(rows: list[list], widths: list[float], numeric_from: int | None = None,
           extra: list[tuple] | None = None, row_heights: list | None = None) -> Table:
    """Header row + data rows; strings become Paragraphs (right-aligned from column numeric_from)."""
    data = []
    for r_index, row in enumerate(rows):
        cells = []
        for c_index, cell in enumerate(row):
            if isinstance(cell, Paragraph):
                cells.append(cell)
                continue
            right = numeric_from is not None and c_index >= numeric_from and r_index > 0
            cells.append(P(cell, CELL_R if right else CELL, bold=r_index == 0))
        data.append(cells)
    table = Table(data, colWidths=widths, rowHeights=row_heights, repeatRows=1, hAlign="LEFT")
    table.setStyle(_GRID)
    if extra:
        table.setStyle(TableStyle(extra))
    return table


# --- Page footer with "Side x av y" -----------------------------------------------------------


class _InsertMarker(Flowable):
    """Invisible marker: the pages of `path` are inserted after the page it lands on."""

    def __init__(self, registry: list, path: Path, pages: int):
        super().__init__()
        self.registry, self.path, self.pages = registry, path, pages

    def wrap(self, *_args):
        return 0, 0

    def draw(self):
        self.registry.append((self.canv.getPageNumber(), self.path, self.pages))


def _numbered_canvas(footer_lines: list[str], insertions: list | None = None, appended: int = 0):
    inserted = insertions if insertions is not None else []

    class NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            # The markers have all been drawn by now, so inserted pages can be counted in.
            total = len(self._pages) + sum(n for _page, _path, n in inserted) + appended
            for state in self._pages:
                self.__dict__.update(state)
                own = self._pageNumber
                self._draw_footer(own + sum(n for page, _path, n in inserted if page < own), total)
                super().showPage()
            super().save()

        def _draw_footer(self, number: int, total: int) -> None:
            self.setStrokeColor(colors.HexColor("#999999"))
            self.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
            y = 10.5 * mm
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.black)
            self.drawString(MARGIN, y, plain_text(footer_lines[0]))
            self.drawRightString(PAGE_W - MARGIN, y, f"Side {number} av {total}")
            for extra in footer_lines[1:]:
                para = Paragraph(pdf_text(extra), ParagraphStyle(
                    "foot", fontName="Helvetica", fontSize=6.6, leading=7.6, textColor=colors.HexColor("#b00020")))
                _, h = para.wrap(CONTENT_W, 10 * mm)
                y -= h + 0.5 * mm
                para.drawOn(self, MARGIN, y)

    return NumberedCanvas


def _build(path: Path, story: list, footer_lines: list[str], title: str, insertions: list | None = None,
           appended: int = 0) -> None:
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
        bottomMargin=26 * mm if len(footer_lines) > 1 else 18 * mm, title=plain_text(title),
    )
    doc.build(story, canvasmaker=_numbered_canvas(footer_lines, insertions, appended))


def _footer(view: PlanView, kind: str) -> list[str]:
    status = "ferdigstilt" if view.finished else "utkast"
    lines = [f"{kind} {view.year} · {view.enterprise.get('name', '')} · versjon {view.version} ({status})"]
    if view.unverified:
        lines.append(f"Planen bygger på ikke-verifiserte normverdier: {view.unverified}")
    return lines


# --- Plan ---------------------------------------------------------------------------------------


def export_plan(view: PlanView, path: Path, data_dir: Path) -> Path:
    story: list = []
    story += _front_page(view)
    insertions: list = []  # (after own page, pdf path, pages) – filled while the PDF is drawn
    story += [PageBreak()] + _map_pages(view, data_dir, insertions)
    story += [PageBreak(), P("Plan per skifte", H1)]
    for row in view.rows:
        story += _field_block(view, row)
    reports = lab_reports(view, data_dir)
    # Own pages: Jordanalyser, Innkjøpsliste, and one journal-form page per field (printed for the shed).
    story += [PageBreak()] + _soil_overview(view, reports)
    story += [PageBreak()] + _purchase_list(view)
    story += [PageBreak()] + _journal_form(view)
    _build(path, story, _footer(view, "Gjødslingsplan"), f"Gjødslingsplan {view.year}", insertions,
           appended=sum(r.pages for r in reports))
    _merge_pdfs(path, insertions, [r.path for r in reports if r.pages])
    return path


@dataclass(frozen=True)
class LabReport:
    """One lab report appended to the plan; a farm-wide report covers several samples."""
    samples: tuple[SampleLine, ...]
    path: Path | None
    pages: int  # 0 = can't be attached, see problem
    problem: str = ""


def lab_reports(view: PlanView, data_dir: Path) -> list[LabReport]:
    """Lab reports (PDF) of soil samples used by fields in the plan, checked before the plan is drawn.

    Utvidet analyse ('utenlandsk') must have one; for Standardpakke 1 it is optional. The same report
    (same file, or an identical copy) is appended once, however many samples it covers.
    """
    groups: dict[object, list] = {}  # key -> [path, pages, problem, samples]
    for s in sorted(view.samples, key=lambda x: (x.sample_date or "", x.label or "")):
        if not s.field_nos or (s.origin != "utenlandsk" and not s.attachment):
            continue
        path = data_dir / s.attachment if s.attachment else None
        key: object = ("missing", s.attachment, id(s))
        if path is None or not path.exists():
            entry = [path, 0, "rapportfilen mangler"]
        elif path.suffix.lower() != ".pdf":
            entry = [path, 0, "bare PDF kan legges ved planen"]
        else:
            key = hashlib.sha256(path.read_bytes()).hexdigest()
            if key in groups:
                groups[key][3].append(s)
                continue
            try:
                reader = PdfReader(str(path))
                if reader.is_encrypted and not reader.decrypt(""):
                    raise ValueError("PDF-en er passordbeskyttet")
                entry = [path, len(reader.pages), ""]
            except Exception as exc:  # a broken file must not stop the export; it is reported in the PDF
                entry = [path, 0, f"kunne ikke leses ({exc})"]
        groups[key] = entry + [[s]]
    return [LabReport(tuple(samples), path, pages, problem) for path, pages, problem, samples in groups.values()]


def _merge_pdfs(path: Path, insertions: list, appendix: list[Path]) -> None:
    """Insert PDF pages after the marked pages (map sketches) and append the rest (lab reports)."""
    if not insertions and not appendix:
        return
    writer = PdfWriter()
    for number, page in enumerate(PdfReader(str(path)).pages, start=1):
        writer.add_page(page)
        for after, extra, _pages in insertions:
            if after == number:
                writer.append(str(extra))
    for f in appendix:
        writer.append(str(f))
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        writer.write(fh)
    os.replace(tmp, path)


def _front_page(view: PlanView) -> list:
    e = view.enterprise
    status = "Ferdigstilt" if view.finished else "Utkast (ikke ferdigstilt)"
    when = format_date((view.finalized_at or view.generated_at or "")[:10]) or format_date(date.today().isoformat())
    out = [P(f"Gjødslingsplan {view.year}", H1),
           P(f"{status} · versjon {view.version} · {when}", BODY, bold=True), Spacer(1, 4 * mm)]
    contact = " ".join(filter(None, (e.get("contact_first_name"), e.get("contact_last_name"))))
    address = ", ".join(filter(None, (e.get("street"), " ".join(filter(None, (e.get("postcode"), e.get("place")))))))
    adv = view.advisor
    info = [
        ["Foretak", e.get("name") or "", "Rådgiver", adv.get("advisor_name") or ""],
        ["Org.nr.", e.get("org_nr") or "", "Firma", adv.get("advisor_company") or ""],
        ["Adresse", address, "Telefon", adv.get("advisor_phone") or ""],
        ["Kontaktperson", contact, "E-post", adv.get("advisor_email") or ""],
        ["Telefon / e-post", " / ".join(filter(None, (e.get("phone"), e.get("email")))), "Region (P-grense)",
         e.get("region") or ""],
    ]
    out.append(Table([[P(c, CELL, bold=i % 2 == 0) for i, c in enumerate(r)] for r in info],
                     colWidths=[30 * mm, 57 * mm, 30 * mm, 57 * mm],
                     style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                       ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc"))])))
    if view.change_reason:
        out += [Spacer(1, 3 * mm), P(f"Endringsbegrunnelse (versjon {view.version}): {view.change_reason}", BODY)]

    out += [P("Eiendommer", H2)]
    rows = [["Gårdsnavn", "Kommunenr.", "Gnr.", "Bnr.", "Fnr.", "Snr.", "Eid/leid", "Kartskisse"]]
    for p in view.properties:
        rows.append([p.get("farm_name") or "", p["kommunenr"], str(p["gnr"]), str(p["bnr"]), str(p["fnr"]),
                     str(p["snr"]), p["tenure"], "vedlagt" if p.get("map_attachment") else "MANGLER"])
    out.append(_table(rows, [42 * mm, 20 * mm, 13 * mm, 13 * mm, 13 * mm, 13 * mm, 18 * mm, 42 * mm]))

    out += [P("Status", H2),
            P(f"{view.legal_text}. Gjødslet areal: {_num(view.fertilized_area)} daa "
              f"({len([r for r in view.rows if r.fertilized])} skifter).", BODY, bold=True),
            P(view.p_summary + ".", BODY if view.p_ok is not False else WARN)]
    for m in view.p_messages:
        out.append(P(f"• {m}", WARN))

    out += [P("Skifter", H2)]
    rows = [["Nr.", "Navn", "Kultur", "Daa", "Stadium", "Behov N", "Behov P", "Behov K",
             "Plan N", "Plan P", "Plan K"]]
    for r in view.rows:
        stage = (r.stage or "–") + ("" if r.fertilized else " (ikke gjødslet)")
        planned = [_num(r.planned_supply[n]) if r.fertilized else "–" for n in "npk"]
        rows.append([r.field_no, r.name, r.culture_name, _num(r.area_daa), stage,
                     *(_num(r.effective[n]) for n in "npk"), *planned])
    out.append(_table(rows, [10 * mm, 32 * mm, 24 * mm, 13 * mm, 18 * mm] + [13.5 * mm] * 6,
                      numeric_from=5, extra=[("ALIGN", (3, 1), (3, -1), "RIGHT")]))

    if view.warnings:
        out += [P("Varsler", H2)] + [P(f"• {w}", WARN) for w in view.warnings]
    return out


def _map_pages(view: PlanView, data_dir: Path, insertions: list) -> list:
    """Per property: the generated map (boundary + soil sample points), then the uploaded gårdskart."""
    out = [P("Kartskisse", H1)]
    for index, p in enumerate(view.properties):
        if index:
            out.append(PageBreak())
        out.append(P(f"{p['label']} – {'leid' if p['tenure'] == 'leid' else 'eid'}", H2))
        generated = p.get("generated_map")
        if generated:
            out += _generated_map(generated, data_dir)
        out += _uploaded_map(p.get("map_attachment"), data_dir, insertions, after_generated=bool(generated))
    return out


def _uploaded_map(relative: str | None, data_dir: Path, insertions: list, after_generated: bool) -> list:
    if not relative:
        return [] if after_generated else [P("KART MANGLER – påkrevd etter § 26", BIG_WARN, bold=True)]
    out = [] if after_generated else [
        P("Generert kart med jordprøvepunkter er ikke hentet (Eiendommer og skifter → Kart…).", SMALL)]
    file = data_dir / relative
    if not file.exists():
        return out + [P(f"{'Opplastet kart' if after_generated else 'KART MANGLER'} – finner ikke filen {relative}"
                        f"{'' if after_generated else ' (påkrevd etter § 26)'}", BIG_WARN, bold=True)]
    if file.suffix.lower() in IMAGE_SUFFIXES:
        if after_generated:
            out += [PageBreak(), P(f"Opplastet gårdskart: {file.name}", H3)]
        try:
            w, h = ImageReader(str(file)).getSize()
            scale = min(CONTENT_W / w, 200 * mm / h)
            out.append(Image(str(file), width=w * scale, height=h * scale))
        except Exception as exc:  # unreadable image: say so instead of failing the whole export
            out.append(P(f"Kunne ikke lese kartfilen {relative}: {exc}", WARN))
        return out
    try:
        reader = PdfReader(str(file))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF-en er passordbeskyttet")
        pages = len(reader.pages)
    except Exception as exc:  # a broken file must not stop the export; it is reported in the PDF
        return out + [P(f"{'Opplastet kart' if after_generated else 'KART MANGLER'} – kartfilen {file.name} "
                        f"kunne ikke leses ({exc}).", BIG_WARN, bold=True)]
    where = "neste side" if pages == 1 else f"de neste {pages} sidene"
    text = f"Opplastet gårdskart ({file.name}) følger på {where}." if after_generated \
        else f"Kartskissen ({file.name}) følger på {where}."
    return out + [P(text, BODY), _InsertMarker(insertions, file, pages)]


def _generated_map(data: dict, data_dir: Path) -> list:
    fm, points, missing = map_service.from_view_data(data)
    background = data_dir / fm.background
    if not background.is_file():
        return [P(f"Generert kart: finner ikke kartbildet {fm.background}. Hent kartet på nytt "
                  "(Eiendommer og skifter → Kart…).", WARN)]
    inside, outside = fm.place(points)
    width = CONTENT_W
    height = width * fm.height / fm.width
    if height > 165 * mm:
        width, height = width * 165 * mm / height, 165 * mm
    out = [P("Eiendomskart med jordprøvepunkter", H3), _FarmMapFlowable(fm, inside, background, width, height),
           Spacer(1, 1.5 * mm),
           P("Rød linje: eiendomsgrense · gul sirkel: jordprøve · blå firkant: skifte. "
             f"{farm_map.BOUNDARY_NOTE} Kart hentet {format_date(fm.fetched_date())}.", SMALL),
           P(farm_map.ATTRIBUTION.get(fm.background_kind, ""), SMALL)]
    for note in fm.notes:
        out.append(P(note, WARN))
    if outside:
        out.append(P("Utenfor kartutsnittet (hent kartet på nytt): " + ", ".join(p.label for p in outside) + ".", WARN))
    if missing:
        out.append(P("Jordprøver uten koordinater (§ 29 krever prøvepunkter med koordinater): "
                     + ", ".join(missing) + ".", WARN))
    away = fm.teiger_outside()
    if away:
        out.append(P(f"{away} teig{'er' if away > 1 else ''} av eiendommen ligger utenfor kartutsnittet.", SMALL))
    samples = sorted((p for p in points if p.kind == "sample"), key=lambda p: p.label.lower())
    if samples:
        rows = [["Jordprøvepunkt", "Koordinater (WGS84)"]] + [[p.label, format_coordinates(p.lat, p.lon)]
                                                              for p in samples]
        out += [Spacer(1, 2 * mm), _table(rows, [70 * mm, 70 * mm])]
    return out


class _FarmMapFlowable(Flowable):
    """Background image with the boundary, points and labels drawn as vectors (sharp, searchable text)."""

    def __init__(self, fm, inside: list, background: Path, width: float, height: float):
        super().__init__()
        self.fm, self.inside, self.background = fm, inside, background
        self.width, self.height = width, height

    def wrap(self, *_args):
        return self.width, self.height

    def draw(self):
        c, w, h = self.canv, self.width, self.height
        c.saveState()
        c.drawImage(str(self.background), 0, 0, w, h)
        clip = c.beginPath()
        clip.rect(0, 0, w, h)
        c.clipPath(clip, stroke=0, fill=0)
        c.setStrokeColor(colors.HexColor("#d0021b"))
        c.setLineWidth(1.4)
        c.setLineJoin(1)
        for ring in self.fm.rings():
            path = c.beginPath()
            path.moveTo(ring[0][0] * w, (1 - ring[0][1]) * h)
            for fx, fy in ring[1:]:
                path.lineTo(fx * w, (1 - fy) * h)
            path.close()
            c.drawPath(path, stroke=1, fill=0)
        for point, fx, fy in sorted(self.inside, key=lambda item: item[0].kind == "sample"):
            x, y = fx * w, (1 - fy) * h
            sample = point.kind == "sample"
            if sample:
                c.setStrokeColor(colors.black)
                c.setLineWidth(0.7)
                c.setFillColor(colors.HexColor("#ffd400"))
                c.circle(x, y, 2.6, stroke=1, fill=1)
            else:
                c.setStrokeColor(colors.white)
                c.setLineWidth(0.6)
                c.setFillColor(colors.HexColor("#1565c0"))
                c.rect(x - 2, y - 2, 4, 4, stroke=1, fill=1)
            font, size = ("Helvetica-Bold", 7.2) if sample else ("Helvetica", 6.6)
            text = plain_text(point.label)
            tw = c.stringWidth(text, font, size)
            if sample:  # right of the point (left near the edge)
                tx, ty = (x + 4.5 if x + 4.5 + tw < w - 2 else x - 4.5 - tw), y - 2.4
            else:  # fields: centred under the square, so they don't collide with sample names
                tx, ty = min(max(x - tw / 2, 2), w - 2 - tw), y - 10
            c.setFillColor(colors.white)
            c.setFillAlpha(0.78)
            c.rect(tx - 1.2, ty - 1.8, tw + 2.4, size + 2.2, stroke=0, fill=1)
            c.setFillAlpha(1)
            c.setFillColor(colors.black if sample else colors.HexColor("#0d3c78"))
            c.setFont(font, size)
            c.drawString(tx, ty, text)
        c.restoreState()
        self._decorations(c, w, h)

    def _decorations(self, c, w, h):
        c.saveState()
        c.setStrokeColor(colors.HexColor("#555555"))
        c.setLineWidth(0.6)
        c.rect(0, 0, w, h, stroke=1, fill=0)
        # scale bar, bottom left
        metres = farm_map.nice_scale_length(self.fm.metres_per_width())
        bar = metres / self.fm.metres_per_width() * w
        c.setFillColor(colors.white)
        c.setFillAlpha(0.85)
        c.rect(5, 5, bar + 10, 15, stroke=0, fill=1)
        c.setFillAlpha(1)
        c.setFillColor(colors.black)
        c.rect(10, 9, bar, 2.5, stroke=0, fill=1)
        c.setFont("Helvetica", 6.5)
        c.drawString(10, 13.5, f"{metres} m")
        # north arrow, top right
        x, y = w - 14, h - 26
        c.setFillColor(colors.white)
        c.setFillAlpha(0.85)
        c.rect(x - 8, y - 4, 16, 26, stroke=0, fill=1)
        c.setFillAlpha(1)
        c.setFillColor(colors.black)
        arrow = c.beginPath()
        arrow.moveTo(x, y + 12)
        arrow.lineTo(x - 4, y)
        arrow.lineTo(x + 4, y)
        arrow.close()
        c.drawPath(arrow, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(x, y + 14, "N")
        c.restoreState()


def _field_block(view: PlanView, r: FieldRow) -> list:
    head = P(f"Skifte {r.field_no} – {r.name}", H2)
    info = [
        ["Kultur / sort", f"{r.culture_name}" + (f" / {r.variety}" if r.variety else ""),
         "Areal", f"{_num(r.area_daa)} daa"],
        ["Stadium", f"{r.stage or '–'}" + (f" (plantet {r.planting_year})" if r.planting_year else ""),
         "Forgrøde", r.forgrode or "–"],
        ["Forventet avling", f"{_num(r.expected_yield_kg_daa, 0)} kg/daa", "Nyvekst", r.growth_label or "ikke satt"],
        ["Jordprøve", r.soil_sample_text, "Eiendom", r.property_label],
    ]
    info_table = Table([[P(c, CELL, bold=i % 2 == 0) for i, c in enumerate(row)] for row in info],
                       colWidths=[28 * mm, 62 * mm, 22 * mm, 62 * mm],
                       style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    block = [head, info_table, Spacer(1, 2 * mm)]
    if not r.fertilized:
        block.append(P(f"Skiftet gjødsles ikke i {view.year}.", BODY, bold=True))
        return [KeepTogether(block)]

    req_rows = [["kg/daa", "N", "P", "K"], ["Beregnet behov", *(_num(r.req[n]) for n in "npk")]]
    if any(v is not None for v in r.override.values()):
        req_rows.append(["Overstyrt", *(_num(r.override[n], empty="") for n in "npk")])
    req_rows.append([P("Gjeldende behov", CELL, bold=True), *(P(_num(r.effective[n]), CELL_R, bold=True) for n in "npk")])
    req_rows.append([P("Planlagt tilført (§ 26 h)", CELL, bold=True),
                     *(P(_num(r.planned_supply[n], empty="ukjent"), CELL_R, bold=n in "np") for n in "npk")])
    req_rows.append(["Balanse", *(_num(r.balance[n], empty="–") for n in "npk")])
    block.append(_table(req_rows, [50 * mm, 25 * mm, 25 * mm, 25 * mm], numeric_from=1))
    if r.override_reason:
        block.append(P(f"Begrunnelse for overstyring: {r.override_reason}", SMALL))
    block += [Spacer(1, 1.5 * mm), P(f"Beregning: {r.rationale}", SMALL)]

    rows = [["Tidspunkt", "Gjødseltype", "kg/daa", "N", "P", "K", "Totalt kg"]]
    for line in r.planned:
        rows.append([line.timing_label, line.product_name, _num(line.kg_per_daa),
                     _num(line.supply["n"], 2), _num(line.supply["p"], 2), _num(line.supply["k"], 2),
                     _num(line.total_kg, 0)])
    if not r.planned:
        rows.append(["", "Ingen planlagt gjødsling", "", "", "", "", ""])
    rows.append([P("Sum", CELL, bold=True), "", "", P(_num(r.planned_supply["n"], 2), CELL_R, bold=True),
                 P(_num(r.planned_supply["p"], 2), CELL_R, bold=True), _num(r.planned_supply["k"], 2), ""])
    block += [Spacer(1, 1.5 * mm),
              _table(rows, [36 * mm, 58 * mm, 16 * mm, 15 * mm, 15 * mm, 15 * mm, 19 * mm], numeric_from=2)]
    notes = [*r.soil_notes, *(w for w in r.warnings if not w.startswith("Bygger på ikke-verifiserte"))]
    for note in notes:
        block.append(P(f"• {note}", SMALL))
    if r.notes:
        block.append(P(f"Notater: {r.notes}", SMALL))
    return [KeepTogether(block[:4]), *block[4:], Spacer(1, 3 * mm)]


def _soil_overview(view: PlanView, reports: list[LabReport]) -> list:
    out = [P("Jordanalyser", H1),
           P(f"Gyldighet vurdert for {view.year}. Bare AL-analyser brukes i beregningen. "
             "P-AL er oppgitt som mg P/100 g.", SMALL), Spacer(1, 2 * mm)]
    keys = ("ph", "p_al", "k_al", "mg_al", "ca_al", "k_hno3", "mold_pct")
    rows = [["Dato", "Merking / lab", "Met.", "pH", "P-AL", "K-AL", "Mg-AL", "Ca-AL", "K-HNO3", "Mold %",
             "Alder", "Status", "Skifter"]]
    opt = view.soil_optimum
    rows.append(["Optimum", "", "", *(f"{format_plain(opt[k]['min'])}–{format_plain(opt[k]['max'])}" if k in opt
                                      else "" for k in keys), "", "", ""])
    invalid_rows = []
    for s in view.samples:
        lab = " / ".join(filter(None, (s.label, s.lab))) + (" (utvidet analyse)" if s.origin == "utenlandsk" else "")
        rows.append([format_date(s.sample_date), lab, s.method,
                     *(_num(s.values.get(k), 1, "") for k in keys), f"{s.age} år",
                     s.validity_text if s.valid else f"UGYLDIG: {s.validity_text}", ", ".join(s.field_nos)])
        if not s.valid:
            invalid_rows.append(len(rows) - 1)
    widths = [17 * mm, 22 * mm, 9 * mm, 13 * mm, 11 * mm, 12 * mm, 12 * mm, 14 * mm, 13 * mm, 11 * mm, 10 * mm,
              22 * mm, 12 * mm]
    extra = [("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f3f3f3"))]
    extra += [("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#888888")) for i in invalid_rows]
    out.append(_table(rows, widths, extra=extra))
    if not view.samples:
        out.append(P("Ingen jordprøver registrert.", WARN))
    if any(s.lat is None for s in view.samples):
        out.append(P("Merk: § 29 krever koordinater for prøvepunktene; noen prøver mangler koordinater.", WARN))
    if reports:
        out += [Spacer(1, 3 * mm), P("Analyserapporter", H3)]
        for r in reports:
            names = ", ".join(f"{format_date(x.sample_date)} {x.label or ''}".strip() for x in r.samples)
            lab = r.samples[0].lab
            if r.pages:
                out.append(P(f"• {r.path.name}{f' ({lab})' if lab else ''} – prøve{'r' if len(r.samples) > 1 else ''} "
                             f"{names}: lagt ved bakerst i planen, {r.pages} side(r).", BODY))
            else:
                out.append(P(f"• {names}: rapporten kunne ikke legges ved – {r.problem}.", WARN))
    return out


def _purchase_list(view: PlanView) -> list:
    prices = {r["row"]["code"]: r["row"].get("price_nok_per_kg")
              for r in view.reference_rows if r["file"] == "products.csv"}
    totals: dict[str, dict] = {}
    for r in view.rows:
        if not r.fertilized:
            continue
        for line in r.planned:
            entry = totals.setdefault(line.product_code, {"name": line.product_name, "vaar": 0.0, "sommer_host": 0.0,
                                                         "other": 0.0})
            entry[line.season if line.season in ("vaar", "sommer_host") else "other"] += line.total_kg
    out = [P("Innkjøpsliste", H1)]
    if not totals:
        return out + [P("Ingen planlagt gjødsling.", BODY)]
    rows = [["Produkt", "Vår kg", "Sommer/høst kg", "Uten tidspunkt kg", "Totalt kg", "Pris kr/kg", "Sum kr"]]
    grand = None
    for code, e in sorted(totals.items(), key=lambda kv: kv[1]["name"]):
        total = e["vaar"] + e["sommer_host"] + e["other"]
        price = prices.get(code)
        cost = total * price if price is not None else None
        if cost is not None:
            grand = (grand or 0.0) + cost
        rows.append([e["name"], _num(e["vaar"], 0), _num(e["sommer_host"], 0), _num(e["other"], 0, ""),
                     _num(total, 0), _num(price, 2, "–"), _num(cost, 0, "–")])
    rows.append([P("Sum", CELL, bold=True), "", "", "", "", "", P(_num(grand, 0, "–"), CELL_R, bold=True)])
    out.append(_table(rows, [55 * mm, 18 * mm, 24 * mm, 26 * mm, 18 * mm, 16 * mm, 17 * mm], numeric_from=1))
    out.append(P("Sum kr omfatter bare produkter med pris i products.csv. Mengdene gjelder skifter som gjødsles.", SMALL))
    return out


def _journal_form(view: PlanView) -> list:
    """One page per field: planned and actual applications plus empty rows to fill in by hand."""
    out = []
    for index, r in enumerate(view.rows):
        if index:
            out.append(PageBreak())
        rows = [["Dato", "Gjødseltype", "Planlagt kg/daa", "Utført kg/daa", "N kg/daa", "P kg/daa", "K kg/daa",
                 "Utført av"]]
        for line in r.planned:
            rows.append([line.timing_label, line.product_name, _num(line.kg_per_daa), "", "", "", "", ""])
        for line in r.actual:
            rows.append([format_date(line.date), line.product_name, "", _num(line.kg_per_daa),
                         *(_num(line.supply[n], 2) for n in "npk"), line.performed_by or ""])
        filled = len(rows)
        rows += [[""] * 8 for _ in range(HAND_ROWS)]
        out += [
            P(f"Gjødseljournal {view.year} – skifte {r.field_no} {r.name}", H2),
            P(f"{view.enterprise.get('name', '')} · {_num(r.area_daa)} daa · {r.culture_name}"
              + (f" · {r.variety}" if r.variety else ""), BODY, bold=True),
            P("Planlagt og utført gjødsling. Tomme linjer kan fylles ut for hånd.", SMALL), Spacer(1, 2 * mm),
            _table(rows, [20 * mm, 46 * mm, 19 * mm, 19 * mm, 16 * mm, 16 * mm, 16 * mm, 24 * mm], numeric_from=2,
                   row_heights=[None] * filled + [7 * mm] * HAND_ROWS),
        ]
    if not view.rows:
        out.append(P("Gjødseljournal – ingen skifter i planen.", H2))
    return out


# --- § 27 journal -------------------------------------------------------------------------------


def export_journal(view: PlanView, path: Path) -> Path:
    e = view.enterprise
    totals = view.journal_totals()
    story = [P(f"Gjødseljournal {view.year}", H1),
             P(f"{e.get('name', '')}" + (f" · org.nr. {e['org_nr']}" if e.get("org_nr") else "")
               + f" · utskrevet {format_date(date.today().isoformat())}", BODY, bold=True),
             P("Journal etter gjødselbrukforskriften § 27: skifte, dato, gjødseltype og mengde, N og P per skifte "
               "og samlet for foretaket. K er tatt med i tillegg (ikke påkrevd).", SMALL), Spacer(1, 3 * mm)]
    for index, f in enumerate(totals["fields"]):
        r = f["row"]
        if index:
            story.append(PageBreak())  # one page per field
        rows = [["Dato", "Gjødseltype", "kg/daa", "Totalt kg", "N kg/daa", "P kg/daa", "K kg/daa",
                 "N kg", "P kg", "K kg"]]
        for line in sorted(r.actual, key=lambda a: a.date or ""):
            per_daa = [line.supply[n] for n in "npk"]
            rows.append([format_date(line.date), line.product_name, _num(line.kg_per_daa), _num(line.total_kg, 0),
                         *(_num(v, 2) for v in per_daa),
                         *(_num(None if v is None else v * r.area_daa, 1) for v in per_daa)])
        if not r.actual:
            rows.append(["", "Ingen gjødsling registrert"] + [""] * 8)
        rows.append([P("Sum skifte", CELL, bold=True), "", "", "",
                     *(P(_num(f[f"{n}_daa"], 2), CELL_R, bold=True) for n in "npk"),
                     *(P(_num(f[f"{n}_kg"], 1), CELL_R, bold=True) for n in "npk")])
        story += [
            P(f"Skifte {r.field_no} – {r.name} ({_num(r.area_daa)} daa, {r.culture_name})", H2),
            _table(rows, [17 * mm, 40 * mm, 13 * mm, 15 * mm] + [14.5 * mm] * 3 + [15.5 * mm] * 3, numeric_from=2),
        ]
    summary = [["Skifte", "Daa", "N kg/daa", "P kg/daa", "K kg/daa", "N kg", "P kg", "K kg"]]
    for f in totals["fields"]:
        r = f["row"]
        summary.append([f"{r.field_no} – {r.name}", _num(r.area_daa), *(_num(f[f"{n}_daa"], 2) for n in "npk"),
                        *(_num(f[f"{n}_kg"], 1) for n in "npk")])
    summary.append([P("Sum for foretaket", CELL, bold=True), P(_num(sum(f["row"].area_daa for f in totals["fields"])),
                                                               CELL_R, bold=True), "", "", "",
                    *(P(_num(totals[f"{n}_kg"], 1), CELL_R, bold=True) for n in "npk")])
    story += [PageBreak(), P("Sum for foretaket", H2),
              _table(summary, [52 * mm, 14 * mm] + [17 * mm] * 3 + [19 * mm] * 3, numeric_from=1)]
    _build(path, story, [f"Gjødseljournal {view.year} · {e.get('name', '')}"], f"Gjødseljournal {view.year}")
    return path


def default_filename(view: PlanView, kind: str) -> str:
    name = "".join(ch if ch.isalnum() else "_" for ch in view.enterprise.get("name", "foretak")).strip("_")
    suffix = f"_v{view.version}" if kind == "Gjodslingsplan" else ""
    return f"{kind}_{name}_{view.year}{suffix}.pdf"

