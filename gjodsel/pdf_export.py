"""PDF export (PLAN §8): the full fertilizer plan and the § 27 fertilizer journal.

Renders a PlanView only, so a finished plan prints exactly as frozen.
Uses reportlab's built-in Helvetica (no font files needed); characters it
cannot encode (subscripts, Unicode minus, ≥, …) are mapped to plain text.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    CondPageBreak,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .plan_view import FieldRow, PlanView
from .units import format_date, format_number, format_plain

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
HAND_ROWS = 3  # empty rows per field in the journal form, for hand entries

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


def _numbered_canvas(footer_lines: list[str]):
    class NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._draw_footer(total)
                super().showPage()
            super().save()

        def _draw_footer(self, total: int) -> None:
            self.setStrokeColor(colors.HexColor("#999999"))
            self.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
            y = 10.5 * mm
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.black)
            self.drawString(MARGIN, y, plain_text(footer_lines[0]))
            self.drawRightString(PAGE_W - MARGIN, y, f"Side {self._pageNumber} av {total}")
            for extra in footer_lines[1:]:
                para = Paragraph(pdf_text(extra), ParagraphStyle(
                    "foot", fontName="Helvetica", fontSize=6.6, leading=7.6, textColor=colors.HexColor("#b00020")))
                _, h = para.wrap(CONTENT_W, 10 * mm)
                y -= h + 0.5 * mm
                para.drawOn(self, MARGIN, y)

    return NumberedCanvas


def _build(path: Path, story: list, footer_lines: list[str], title: str) -> None:
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
        bottomMargin=26 * mm if len(footer_lines) > 1 else 18 * mm, title=plain_text(title),
    )
    doc.build(story, canvasmaker=_numbered_canvas(footer_lines))


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
    story += [PageBreak()] + _map_pages(view, data_dir)
    story += [PageBreak(), P("Plan per skifte", H1)]
    for row in view.rows:
        story += _field_block(view, row)
    story += [PageBreak()] + _soil_overview(view)
    story += [Spacer(1, 6 * mm), CondPageBreak(80 * mm)] + _purchase_list(view)
    story += [PageBreak()] + _journal_form(view)
    _build(path, story, _footer(view, "Gjødslingsplan"), f"Gjødslingsplan {view.year}")
    return path


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
    rows = [["Nr.", "Navn", "Kultur", "Daa", "Stadium", "Behov N", "Behov P", "Planlagt N", "Planlagt P"]]
    for r in view.rows:
        rows.append([r.field_no, r.name, r.culture_name, _num(r.area_daa), r.stage or "–",
                     _num(r.effective["n"]), _num(r.effective["p"]),
                     _num(r.planned_supply["n"]) if r.fertilized else "ikke gjødslet",
                     _num(r.planned_supply["p"]) if r.fertilized else ""])
    out.append(_table(rows, [11 * mm, 38 * mm, 28 * mm, 14 * mm, 20 * mm, 15 * mm, 15 * mm, 17 * mm, 16 * mm],
                      numeric_from=5, extra=[("ALIGN", (3, 1), (3, -1), "RIGHT")]))

    if view.warnings:
        out += [P("Varsler", H2)] + [P(f"• {w}", WARN) for w in view.warnings]
    return out


def _map_pages(view: PlanView, data_dir: Path) -> list:
    out = [P("Kartskisse", H1)]
    for p in view.properties:
        out.append(P(f"{p['label']} – {'leid' if p['tenure'] == 'leid' else 'eid'}", H2))
        relative = p.get("map_attachment")
        if not relative:
            out.append(P("KART MANGLER – påkrevd etter § 26", BIG_WARN, bold=True))
            continue
        file = data_dir / relative
        if not file.exists():
            out.append(P(f"KART MANGLER – finner ikke filen {relative} (påkrevd etter § 26)", BIG_WARN, bold=True))
        elif file.suffix.lower() in IMAGE_SUFFIXES:
            try:
                w, h = ImageReader(str(file)).getSize()
                scale = min(CONTENT_W / w, 200 * mm / h)
                out.append(Image(str(file), width=w * scale, height=h * scale))
            except Exception as exc:  # unreadable image: say so instead of failing the whole export
                out.append(P(f"Kunne ikke lese kartfilen {relative}: {exc}", WARN))
        else:
            out.append(P(f"Kartskissen er vedlagt som egen fil: {file.name} (legg den ved planen).", BODY, bold=True))
    return out


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


def _soil_overview(view: PlanView) -> list:
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
        rows.append([format_date(s.sample_date), " / ".join(filter(None, (s.label, s.lab))), s.method,
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
    out = [P("Gjødseljournal – skjema per skifte", H1),
           P("Planlagt og utført gjødsling. Tomme linjer kan fylles ut for hånd.", SMALL)]
    for r in view.rows:
        rows = [["Dato", "Gjødseltype", "Planlagt kg/daa", "Utført kg/daa", "N kg/daa", "P kg/daa", "Utført av"]]
        for line in r.planned:
            rows.append([line.timing_label, line.product_name, _num(line.kg_per_daa), "", "", "", ""])
        for line in r.actual:
            rows.append([format_date(line.date), line.product_name, "", _num(line.kg_per_daa),
                         _num(line.supply["n"], 2), _num(line.supply["p"], 2), line.performed_by or ""])
        filled = len(rows)
        rows += [[""] * 7 for _ in range(HAND_ROWS)]
        out.append(KeepTogether([
            P(f"Skifte {r.field_no} – {r.name} ({_num(r.area_daa)} daa, {r.culture_name})", H3),
            _table(rows, [22 * mm, 50 * mm, 22 * mm, 22 * mm, 17 * mm, 17 * mm, 24 * mm], numeric_from=2,
                   row_heights=[None] * filled + [7 * mm] * HAND_ROWS),
        ]))
    return out


# --- § 27 journal -------------------------------------------------------------------------------


def export_journal(view: PlanView, path: Path) -> Path:
    e = view.enterprise
    totals = view.journal_totals()
    story = [P(f"Gjødseljournal {view.year}", H1),
             P(f"{e.get('name', '')}" + (f" · org.nr. {e['org_nr']}" if e.get("org_nr") else "")
               + f" · utskrevet {format_date(date.today().isoformat())}", BODY, bold=True),
             P("Journal etter gjødselbrukforskriften § 27: skifte, dato, gjødseltype og mengde, N og P per skifte "
               "og samlet for foretaket.", SMALL), Spacer(1, 3 * mm)]
    for f in totals["fields"]:
        r = f["row"]
        rows = [["Dato", "Gjødseltype", "kg/daa", "Totalt kg", "N kg/daa", "P kg/daa", "N kg", "P kg"]]
        for line in sorted(r.actual, key=lambda a: a.date or ""):
            n, p = line.supply["n"], line.supply["p"]
            rows.append([format_date(line.date), line.product_name, _num(line.kg_per_daa), _num(line.total_kg, 0),
                         _num(n, 2), _num(p, 2), _num(None if n is None else n * r.area_daa, 1),
                         _num(None if p is None else p * r.area_daa, 1)])
        if not r.actual:
            rows.append(["", "Ingen gjødsling registrert", "", "", "", "", "", ""])
        rows.append([P("Sum skifte", CELL, bold=True), "", "", "", P(_num(f["n_daa"], 2), CELL_R, bold=True),
                     P(_num(f["p_daa"], 2), CELL_R, bold=True), P(_num(f["n_kg"], 1), CELL_R, bold=True),
                     P(_num(f["p_kg"], 1), CELL_R, bold=True)])
        story.append(KeepTogether([
            P(f"Skifte {r.field_no} – {r.name} ({_num(r.area_daa)} daa, {r.culture_name})", H3),
            _table(rows, [20 * mm, 52 * mm, 15 * mm, 18 * mm, 17 * mm, 17 * mm, 17 * mm, 17 * mm], numeric_from=2),
        ]))
    story += [P("Sum for foretaket", H2),
              _table([["", "N", "P"],
                      ["Totalt tilført (kg)", _num(totals["n_kg"], 1), _num(totals["p_kg"], 1)]],
                     [60 * mm, 30 * mm, 30 * mm], numeric_from=1)]
    _build(path, story, [f"Gjødseljournal {view.year} · {e.get('name', '')}"], f"Gjødseljournal {view.year}")
    return path


def default_filename(view: PlanView, kind: str) -> str:
    name = "".join(ch if ch.isalnum() else "_" for ch in view.enterprise.get("name", "foretak")).strip("_")
    suffix = f"_v{view.version}" if kind == "Gjodslingsplan" else ""
    return f"{kind}_{name}_{view.year}{suffix}.pdf"

