"""Load and validate the reference CSVs in data/reference/.

All agronomic numbers come from these files; code only reads them. Loading
collects every problem it finds and raises ReferenceDataError with the full
list (in Norwegian, for the error dialog), so the advisor can fix the files
in one go.
"""

from __future__ import annotations

import csv
import io
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .units import format_plain, parse_number

DEFAULT_REGION = "standard"
TIMING_SEASONS = ("vaar", "sommer_host")
SOIL_PARAMETERS = ("ph", "p_al", "k_al", "mg_al", "ca_al")

_UNVERIFIED_RE = re.compile(r"\bANTATT\b|\bSJEKK ETIKETT\b", re.IGNORECASE)
_EPS = 1e-9


def is_unverified(source: str | None) -> bool:
    """A row is unverified if its source is empty or says ANTATT / SJEKK ETIKETT."""
    text = (source or "").strip()
    return not text or bool(_UNVERIFIED_RE.search(text))


class ReferenceDataError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


class _Sourced:
    source: str

    @property
    def unverified(self) -> bool:
        return is_unverified(self.source)


# --- Row types (field names match the CSV headers) ---------------------------


@dataclass(frozen=True)
class Culture(_Sourced):
    code: str
    name_nb: str
    n_base: float
    p_base: float
    k_base: float
    yield_basis_kg_daa: float
    n_per_tonne: float
    default_yield_kg_daa: float
    source: str


@dataclass(frozen=True)
class PAlBand(_Sourced):
    pal_min: float
    pal_max: float
    pct: float
    source: str


@dataclass(frozen=True)
class KCorrection(_Sourced):
    khno3_min: float
    khno3_max: float
    kal_min: float
    kal_max: float
    pct: float
    use_when_hno3_missing: bool
    source: str


@dataclass(frozen=True)
class GrowthAdjustment(_Sourced):
    growth_class: str
    label_nb: str
    n_adj: float
    source: str


@dataclass(frozen=True)
class AgeFactor(_Sourced):
    years_min: int
    years_max: int
    stage: str
    n_factor: float
    p_factor: float
    k_factor: float
    apply_yield_adj: bool
    source: str


@dataclass(frozen=True)
class MoldBand(_Sourced):
    mold_min: float
    mold_max: float
    n_adj: float
    source: str


@dataclass(frozen=True)
class PLimit(_Sourced):
    region: str
    year_from: int
    year_to: int
    kg_p_daa: float
    source: str


@dataclass(frozen=True)
class Product(_Sourced):
    code: str
    name: str
    n: float
    p: float
    k: float
    mg: float | None
    s: float | None
    ca: float | None
    b: float | None
    price_nok_per_kg: float | None
    source: str


@dataclass(frozen=True)
class TemplateRow(_Sourced):
    template: str
    stage: str
    order: int
    product_code: str
    kg_per_daa: float
    timing_code: str
    source: str


@dataclass(frozen=True)
class Timing:
    code: str
    label_nb: str
    season: str
    sort: int


@dataclass(frozen=True)
class SoilOptimum(_Sourced):
    parameter: str
    min: float
    max: float
    source: str


@dataclass(frozen=True)
class RefTables:
    cultures: dict[str, Culture]
    p_al_correction: tuple[PAlBand, ...]
    k_correction: tuple[KCorrection, ...]
    n_growth_adjustment: dict[str, GrowthAdjustment]
    age_factors: tuple[AgeFactor, ...]
    n_mold_correction: tuple[MoldBand, ...]
    p_limits: tuple[PLimit, ...]
    products: dict[str, Product]
    templates: tuple[TemplateRow, ...]
    timings: dict[str, Timing]
    soil_optimum: dict[str, SoilOptimum]

    # Lookups return None when nothing matches; callers turn that into a visible warning.

    def age_factor(self, years: int) -> AgeFactor | None:
        return next((r for r in self.age_factors if r.years_min <= years <= r.years_max), None)

    def mature_age_factor(self) -> AgeFactor:
        """The open-ended oldest band (bærende), used when the planting year is unknown."""
        return max(self.age_factors, key=lambda r: r.years_max)

    def p_al_band(self, p_al: float) -> PAlBand | None:
        return next((r for r in self.p_al_correction if r.pal_min <= p_al < r.pal_max), None)

    def mold_band(self, mold_pct: float) -> MoldBand | None:
        return next((r for r in self.n_mold_correction if r.mold_min <= mold_pct < r.mold_max), None)

    def k_correction_for(self, k_al: float, k_hno3: float | None) -> KCorrection | None:
        """Cell for K-AL and K-HNO₃; with K-HNO₃ missing, the row flagged use_when_hno3_missing."""
        for r in self.k_correction:
            if not r.kal_min <= k_al < r.kal_max:
                continue
            if k_hno3 is None:
                if r.use_when_hno3_missing:
                    return r
            elif r.khno3_min <= k_hno3 < r.khno3_max:
                return r
        return None

    def p_limit(self, region: str, year: int) -> PLimit | None:
        return next((r for r in self.p_limits if r.region == region and r.year_from <= year <= r.year_to), None)

    def unverified_rows(self) -> list[tuple[str, _Sourced]]:
        """(file name, row) for every row whose source is ANTATT / SJEKK ETIKETT / empty."""
        found = []
        for attr, table in _TABLES.items():
            value = getattr(self, attr)
            rows = value.values() if isinstance(value, dict) else value
            found.extend((table.file, r) for r in rows if isinstance(r, _Sourced) and r.unverified)
        return found


# --- File specs ---------------------------------------------------------------


@dataclass(frozen=True)
class _Col:
    name: str
    kind: str  # 'str' | 'float' | 'int' | 'flag'
    required: bool = True


def _s(name: str) -> _Col:
    return _Col(name, "str")


def _f(name: str, required: bool = True) -> _Col:
    return _Col(name, "float", required)


def _i(name: str) -> _Col:
    return _Col(name, "int")


def _flag(name: str) -> _Col:
    return _Col(name, "flag")


_SOURCE = _Col("source", "str", required=False)


@dataclass(frozen=True)
class _Table:
    file: str
    row_type: type
    columns: tuple[_Col, ...]


_TABLES: dict[str, _Table] = {
    "cultures": _Table("cultures.csv", Culture, (
        _s("code"), _s("name_nb"), _f("n_base"), _f("p_base"), _f("k_base"),
        _f("yield_basis_kg_daa"), _f("n_per_tonne"), _f("default_yield_kg_daa"), _SOURCE,
    )),
    "p_al_correction": _Table("p_al_correction.csv", PAlBand, (
        _f("pal_min"), _f("pal_max"), _f("pct"), _SOURCE,
    )),
    "k_correction": _Table("k_correction.csv", KCorrection, (
        _f("khno3_min"), _f("khno3_max"), _f("kal_min"), _f("kal_max"), _f("pct"),
        _flag("use_when_hno3_missing"), _SOURCE,
    )),
    "n_growth_adjustment": _Table("n_growth_adjustment.csv", GrowthAdjustment, (
        _s("growth_class"), _s("label_nb"), _f("n_adj"), _SOURCE,
    )),
    "age_factors": _Table("age_factors.csv", AgeFactor, (
        _i("years_min"), _i("years_max"), _s("stage"), _f("n_factor"), _f("p_factor"),
        _f("k_factor"), _flag("apply_yield_adj"), _SOURCE,
    )),
    "n_mold_correction": _Table("n_mold_correction.csv", MoldBand, (
        _f("mold_min"), _f("mold_max"), _f("n_adj"), _SOURCE,
    )),
    "p_limits": _Table("p_limits.csv", PLimit, (
        _s("region"), _i("year_from"), _i("year_to"), _f("kg_p_daa"), _SOURCE,
    )),
    "products": _Table("products.csv", Product, (
        _s("code"), _s("name"), _f("n"), _f("p"), _f("k"),
        _f("mg", False), _f("s", False), _f("ca", False), _f("b", False),
        _f("price_nok_per_kg", False), _SOURCE,
    )),
    "templates": _Table("templates.csv", TemplateRow, (
        _s("template"), _s("stage"), _i("order"), _s("product_code"), _f("kg_per_daa"),
        _s("timing_code"), _SOURCE,
    )),
    "timings": _Table("timings.csv", Timing, (
        _s("code"), _s("label_nb"), _s("season"), _i("sort"),
    )),
    "soil_optimum": _Table("soil_optimum.csv", SoilOptimum, (
        _s("parameter"), _f("min"), _f("max"), _SOURCE,
    )),
}


# --- Loading ------------------------------------------------------------------


def load_reference(ref_dir: Path) -> RefTables:
    errors: list[str] = []
    loaded = {attr: _read_table(ref_dir / t.file, t, errors) for attr, t in _TABLES.items()}
    _validate(loaded, errors)
    if errors:
        raise ReferenceDataError(errors)
    return RefTables(
        cultures={r.code: r for r in loaded["cultures"]},
        p_al_correction=tuple(sorted(loaded["p_al_correction"], key=lambda r: r.pal_min)),
        k_correction=tuple(loaded["k_correction"]),
        n_growth_adjustment={r.growth_class: r for r in loaded["n_growth_adjustment"]},
        age_factors=tuple(sorted(loaded["age_factors"], key=lambda r: r.years_min)),
        n_mold_correction=tuple(sorted(loaded["n_mold_correction"], key=lambda r: r.mold_min)),
        p_limits=tuple(loaded["p_limits"]),
        products={r.code: r for r in loaded["products"]},
        templates=tuple(loaded["templates"]),
        timings={r.code: r for r in sorted(loaded["timings"], key=lambda r: r.sort)},
        soil_optimum={r.parameter: r for r in loaded["soil_optimum"]},
    )


def _read_table(path: Path, table: _Table, errors: list[str]) -> list | None:
    """Parse one CSV into row objects. Returns None if the file has any error."""
    name = path.name
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        errors.append(f"{name}: filen mangler i {path.parent}")
        return None
    except UnicodeDecodeError:
        errors.append(
            f"{name}: filen er ikke lagret som UTF-8. "
            "Lagre den som «CSV UTF-8» med semikolon som skilletegn."
        )
        return None

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    header = [(h or "").strip().lstrip("\ufeff") for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    missing = [c.name for c in table.columns if c.name not in header]
    if missing:
        errors.append(f"{name}: mangler kolonne(r) {', '.join(missing)} (fant: {';'.join(header)})")
        return None
    duplicated = [h for h, n in Counter(header).items() if h and n > 1]
    if duplicated:
        errors.append(f"{name}: kolonne(r) {', '.join(duplicated)} finnes flere ganger")
        return None

    rows = []
    ok = True
    for raw in reader:
        line = reader.line_num
        if None in raw:
            errors.append(
                f"{name} linje {line}: for mange felt. "
                "Tekst som inneholder semikolon må stå i anførselstegn."
            )
            ok = False
            continue
        if not any((v or "").strip() for v in raw.values()):
            continue  # blank line, e.g. ';;;;' left by Excel
        values, row_errors = _parse_row(raw, table.columns)
        if row_errors:
            errors.extend(f"{name} linje {line}: {e}" for e in row_errors)
            ok = False
            continue
        rows.append(table.row_type(**values))
    if ok and not rows:
        errors.append(f"{name}: ingen datarader")
        ok = False
    return rows if ok else None


def _parse_row(raw: dict[str, str], columns: tuple[_Col, ...]) -> tuple[dict, list[str]]:
    values: dict = {}
    errs: list[str] = []
    for col in columns:
        cell = (raw.get(col.name) or "").strip()
        if not cell:
            if col.required:
                errs.append(f"«{col.name}» er tom")
            values[col.name] = "" if col.kind == "str" else None
            continue
        if col.kind == "str":
            values[col.name] = cell
            continue
        try:
            number = parse_number(cell)
        except ValueError:
            errs.append(f"«{col.name}» = «{cell}» er ikke et tall")
            continue
        if col.kind == "float":
            values[col.name] = number
        elif col.kind == "int":
            if number.is_integer():
                values[col.name] = int(number)
            else:
                errs.append(f"«{col.name}» = «{cell}» må være et heltall")
        elif number in (0, 1):
            values[col.name] = bool(number)
        else:
            errs.append(f"«{col.name}» = «{cell}» må være 0 eller 1")
    return values, errs


# --- Validation ---------------------------------------------------------------


def _validate(t: dict[str, list | None], errors: list[str]) -> None:
    """Content checks. A table that failed to parse is skipped, as are
    cross-file checks that depend on it, to avoid follow-on noise."""

    if (rows := t["cultures"]) is not None:
        _check_unique(errors, "cultures.csv", "code", [r.code for r in rows])
        for r in rows:
            for attr in ("n_base", "p_base", "k_base", "n_per_tonne"):
                _check_min(errors, f"cultures.csv ({r.code})", attr, getattr(r, attr), 0)
            for attr in ("yield_basis_kg_daa", "default_yield_kg_daa"):
                _check_min(errors, f"cultures.csv ({r.code})", attr, getattr(r, attr), 0, strict=True)

    if (rows := t["p_al_correction"]) is not None:
        _check_bands(errors, "p_al_correction.csv", [(r.pal_min, r.pal_max) for r in rows])

    if (rows := t["k_correction"]) is not None:
        _check_k_grid(errors, rows)

    if (rows := t["n_growth_adjustment"]) is not None:
        _check_unique(errors, "n_growth_adjustment.csv", "growth_class", [r.growth_class for r in rows])

    if (rows := t["age_factors"]) is not None:
        _check_int_ranges(errors, "age_factors.csv", [(r.years_min, r.years_max) for r in rows], start=0)
        _check_unique(errors, "age_factors.csv", "stage", [r.stage for r in rows])
        for r in rows:
            for attr in ("n_factor", "p_factor", "k_factor"):
                _check_min(errors, f"age_factors.csv ({r.stage})", attr, getattr(r, attr), 0)

    if (rows := t["n_mold_correction"]) is not None:
        _check_bands(errors, "n_mold_correction.csv", [(r.mold_min, r.mold_max) for r in rows])

    if (rows := t["p_limits"]) is not None:
        regions: dict[str, list[PLimit]] = {}
        for r in rows:
            regions.setdefault(r.region, []).append(r)
            _check_min(errors, f"p_limits.csv ({r.region} {r.year_from})", "kg_p_daa", r.kg_p_daa, 0)
        for region, region_rows in regions.items():
            _check_int_ranges(
                errors, f"p_limits.csv ({region})", [(r.year_from, r.year_to) for r in region_rows]
            )
        if DEFAULT_REGION not in regions:
            errors.append(f"p_limits.csv: regionen «{DEFAULT_REGION}» mangler")

    if (rows := t["products"]) is not None:
        _check_unique(errors, "products.csv", "code", [r.code for r in rows])
        for r in rows:
            for attr in ("n", "p", "k", "mg", "s", "ca", "b"):
                value = getattr(r, attr)
                if value is not None and not 0 <= value <= 100:
                    errors.append(f"products.csv ({r.code}): «{attr}» = {_fmt(value)} må være 0–100 %")
            if r.price_nok_per_kg is not None:
                _check_min(errors, f"products.csv ({r.code})", "price_nok_per_kg", r.price_nok_per_kg, 0)

    if (rows := t["timings"]) is not None:
        _check_unique(errors, "timings.csv", "code", [r.code for r in rows])
        for r in rows:
            if r.season not in TIMING_SEASONS:
                errors.append(
                    f"timings.csv ({r.code}): season «{r.season}» må være en av {', '.join(TIMING_SEASONS)}"
                )

    if (rows := t["soil_optimum"]) is not None:
        _check_unique(errors, "soil_optimum.csv", "parameter", [r.parameter for r in rows])
        for r in rows:
            if r.parameter not in SOIL_PARAMETERS:
                errors.append(
                    f"soil_optimum.csv: ukjent parameter «{r.parameter}» "
                    f"(gyldige: {', '.join(SOIL_PARAMETERS)})"
                )
            if r.min > r.max:
                errors.append(f"soil_optimum.csv ({r.parameter}): min er større enn max")

    if (rows := t["templates"]) is not None:
        where = "templates.csv"
        _check_unique(errors, where, "template+order", [f"{r.template} #{r.order}" for r in rows])
        stages_by_template: dict[str, set[str]] = {}
        for r in rows:
            stages_by_template.setdefault(r.template, set()).add(r.stage)
            _check_min(errors, f"{where} ({r.template} #{r.order})", "kg_per_daa", r.kg_per_daa, 0)
        for name, stages in stages_by_template.items():
            if len(stages) > 1:
                errors.append(f"{where}: malen «{name}» har flere stadier ({', '.join(sorted(stages))})")
        refs = (
            ("products", "product_code", lambda x: x.code),
            ("timings", "timing_code", lambda x: x.code),
            ("age_factors", "stage", lambda x: x.stage),
        )
        for attr, column, key in refs:
            if t[attr] is None:
                continue
            known = {key(x) for x in t[attr]}
            for r in rows:
                if getattr(r, column) not in known:
                    errors.append(
                        f"{where} ({r.template} #{r.order}): {column} «{getattr(r, column)}» "
                        f"finnes ikke i {_TABLES[attr].file}"
                    )


def _check_unique(errors: list[str], where: str, column: str, values: list[str]) -> None:
    for value, n in Counter(values).items():
        if n > 1:
            errors.append(f"{where}: {column} «{value}» finnes {n} ganger")


def _check_min(errors: list[str], where: str, attr: str, value: float, minimum: float,
               strict: bool = False) -> None:
    if value < minimum or (strict and value == minimum):
        op = ">" if strict else "≥"
        errors.append(f"{where}: «{attr}» = {_fmt(value)} må være {op} {_fmt(minimum)}")


def _check_bands(errors: list[str], where: str, pairs: list[tuple[float, float]],
                 start: float = 0.0) -> None:
    """Half-open [min, max) bands must cover from `start` with no gaps or overlaps."""
    expected = start
    for lo, hi in sorted(pairs):
        if hi <= lo:
            errors.append(f"{where}: intervallet {_fmt(lo)}–{_fmt(hi)} er tomt eller snudd")
        if lo > expected + _EPS:
            errors.append(f"{where}: hull – {_fmt(expected)}–{_fmt(lo)} er ikke dekket")
        elif lo < expected - _EPS:
            errors.append(f"{where}: overlapp – {_fmt(lo)}–{_fmt(min(hi, expected))} er dekket flere ganger")
        expected = max(expected, hi)


def _check_int_ranges(errors: list[str], where: str, pairs: list[tuple[int, int]],
                      start: int | None = None) -> None:
    """Inclusive integer ranges must be contiguous: next min = previous max + 1."""
    ordered = sorted(pairs)
    expected = ordered[0][0] if start is None else start
    for lo, hi in ordered:
        if hi < lo:
            errors.append(f"{where}: intervallet {lo}–{hi} er snudd")
        if lo > expected:
            errors.append(f"{where}: hull – {expected}–{lo - 1} er ikke dekket")
        elif lo < expected:
            errors.append(f"{where}: overlapp – {lo}–{min(hi, expected - 1)} er dekket flere ganger")
        expected = max(expected, hi + 1)


def _check_k_grid(errors: list[str], rows: list[KCorrection]) -> None:
    """Every K-HNO₃ band × K-AL band cell must exist exactly once, and exactly
    one K-HNO₃ band is the fallback when K-HNO₃ is missing."""
    where = "k_correction.csv"
    h_bands = sorted({(r.khno3_min, r.khno3_max) for r in rows})
    k_bands = sorted({(r.kal_min, r.kal_max) for r in rows})
    _check_bands(errors, f"{where} (K-HNO₃)", h_bands)
    _check_bands(errors, f"{where} (K-AL)", k_bands)

    cells = Counter(((r.khno3_min, r.khno3_max), (r.kal_min, r.kal_max)) for r in rows)
    for (h, k), n in cells.items():
        if n > 1:
            errors.append(f"{where}: K-HNO₃ {_band(h)} / K-AL {_band(k)} finnes {n} ganger")
    for h in h_bands:
        for k in k_bands:
            if (h, k) not in cells:
                errors.append(f"{where}: mangler verdi for K-HNO₃ {_band(h)} / K-AL {_band(k)}")

    flagged = {(r.khno3_min, r.khno3_max) for r in rows if r.use_when_hno3_missing}
    if len(flagged) != 1:
        errors.append(
            f"{where}: nøyaktig ett K-HNO₃-intervall skal ha use_when_hno3_missing = 1 "
            f"(fant {len(flagged)})"
        )
    else:
        (band,) = flagged
        if any((r.khno3_min, r.khno3_max) == band and not r.use_when_hno3_missing for r in rows):
            errors.append(f"{where}: alle rader i K-HNO₃ {_band(band)} må ha use_when_hno3_missing = 1")


def _fmt(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _band(pair: tuple[float, float]) -> str:
    return f"{_fmt(pair[0])}–{_fmt(pair[1])}"


# --- Writing (product editor) ----------------------------------------------------------------


def save_products(ref_dir: Path, products: list[Product]) -> RefTables:
    """Rewrite products.csv (UTF-8 with BOM for Excel, ';', decimal comma) and reload everything.

    If the result doesn't validate (e.g. a template refers to a removed product),
    the previous file is restored and ReferenceDataError is raised.
    Extra columns an advisor added in Excel are not preserved.
    """
    path = ref_dir / _TABLES["products"].file
    columns = [c.name for c in _TABLES["products"].columns]
    previous = path.read_bytes() if path.exists() else None
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\n")
        writer.writerow(columns)
        for p in products:
            writer.writerow([_csv_value(getattr(p, c)) for c in columns])
    os.replace(tmp, path)
    try:
        return load_reference(ref_dir)
    except ReferenceDataError:
        if previous is None:
            path.unlink()
        else:
            path.write_bytes(previous)
        raise


def _csv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return format_plain(value)
    return str(value)


REFERENCE_FILES = tuple(t.file for t in _TABLES.values())


def table_columns(file: str) -> tuple[_Col, ...]:
    """Column specs (name, kind, required) for a reference file."""
    for t in _TABLES.values():
        if t.file == file:
            return t.columns
    raise KeyError(file)
