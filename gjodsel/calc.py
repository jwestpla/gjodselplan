"""Nutrient requirement per field (PLAN §6). Pure: no tkinter, no database.

Every number comes from RefTables (the reference CSVs) or Settings. When a
value is missing the result carries a warning instead of a fallback number.
Each step of the calculation is recorded as a Step; the steps are the audit
trail and are rendered as the Norwegian rationale text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Settings
from .reference import Culture, RefTables
from .soil import sample_validity, soil_notes
from .units import format_number, format_plain, format_signed, round_half_up

CultureNorm = Culture  # PLAN §6 name for the culture row

NO_VALID_SOIL = "Mangler gyldig jordprøve – planen er mangelfull etter § 26"
UNVERIFIED_PREFIX = "Bygger på ikke-verifiserte normverdier"


@dataclass(frozen=True)
class FieldInput:
    area_daa: float
    culture_code: str
    planting_year: int | None
    expected_yield_kg_daa: float | None
    growth_class: str | None


@dataclass(frozen=True)
class SoilInput:
    sample_date: str  # ISO date
    method: str
    p_al: float | None = None
    k_al: float | None = None
    k_hno3: float | None = None
    mold_pct: float | None = None
    ph: float | None = None
    mg_al: float | None = None
    ca_al: float | None = None


@dataclass(frozen=True)
class Step:
    nutrient: str  # 'N' | 'P' | 'K'
    label_nb: str
    delta: float  # change in kg/daa made by this step
    value_after: float
    kind: str = "add"  # 'start' | 'add' | 'pct' | 'factor' | 'clamp' | 'note' — controls the rationale text


@dataclass
class Requirement:
    n: float | None
    p: float | None
    k: float | None
    stage: str | None
    warnings: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    soil_notes: list[str] = field(default_factory=list)
    soil_valid: bool = False  # False -> the field is "mangelfull" under § 26
    reference_rows: list[tuple[str, object]] = field(default_factory=list)  # (file, row) used

    @property
    def unverified_rows(self) -> list[tuple[str, object]]:
        return [(f, r) for f, r in self.reference_rows if getattr(r, "unverified", False)]

    @property
    def rationale(self) -> str:
        return rationale_text(self.steps, {"N": self.n, "P": self.p, "K": self.k})


def calculate_requirement(fi: FieldInput, soil: SoilInput | None, plan_year: int,
                          ref: RefTables, settings: Settings) -> Requirement:
    req = Requirement(n=None, p=None, k=None, stage=None)
    used = req.reference_rows

    culture = ref.cultures.get(fi.culture_code)
    if culture is None:
        req.warnings.append(f"Kulturen «{fi.culture_code}» finnes ikke i cultures.csv – kan ikke beregne behov.")
        return req
    used.append(("cultures.csv", culture))

    # 1. Stage from age
    if fi.planting_year is None:
        age = ref.mature_age_factor()
        req.warnings.append(f"Planteår mangler – antar {age.stage}.")
    else:
        years = plan_year - fi.planting_year
        if years < 0:
            req.warnings.append(f"Planteår {fi.planting_year} er etter planåret {plan_year} – kan ikke beregne behov.")
            return req
        age = ref.age_factor(years)
        if age is None:
            req.warnings.append(f"Ingen rad i age_factors.csv dekker {years} år etter planting – kan ikke beregne behov.")
            return req
    used.append(("age_factors.csv", age))
    req.stage = age.stage

    # 2. Soil validity
    valid_soil = None
    if soil is None:
        req.warnings.append(NO_VALID_SOIL + ".")
    else:
        validity = sample_validity(soil.method, int(soil.sample_date[:4]), plan_year, settings.soil_max_age_years)
        if validity.valid:
            valid_soil = soil
            if validity.expires_soon:
                req.warnings.append(
                    f"Jordprøven fra {soil.sample_date[:4]} utløper snart (gyldig i {settings.soil_max_age_years} år)."
                )
        else:
            req.warnings.append(f"{NO_VALID_SOIL} (jordprøven er {validity.reason}).")
    req.soil_valid = valid_soil is not None

    # 3. N
    steps = req.steps
    n = culture.n_base
    steps.append(Step("N", f"norm {format_number(n)}", 0.0, n, "start"))
    if age.apply_yield_adj:
        expected = fi.expected_yield_kg_daa
        if expected is None:
            expected = culture.default_yield_kg_daa
            req.warnings.append(
                f"Forventet avling mangler – bruker standard {format_number(expected, 0)} kg/daa fra cultures.csv."
            )
        delta = (expected - culture.yield_basis_kg_daa) / 1000 * culture.n_per_tonne
        n += delta
        steps.append(Step("N", f"avling {format_number(expected, 0)} kg", delta, n))
    if fi.growth_class is None:
        req.warnings.append("Nyvekstklasse er ikke satt – ingen N-justering for nyvekst.")
    else:
        growth = ref.n_growth_adjustment.get(fi.growth_class)
        if growth is None:
            req.warnings.append(f"Nyvekstklasse «{fi.growth_class}» finnes ikke i n_growth_adjustment.csv.")
        else:
            used.append(("n_growth_adjustment.csv", growth))
            n += growth.n_adj
            steps.append(Step("N", _lower_first(growth.label_nb), growth.n_adj, n))
    if settings.apply_mold_correction:
        if valid_soil is None or valid_soil.mold_pct is None:
            req.warnings.append("Moldkorreksjon er slått på, men gyldig jordprøve med moldinnhold mangler.")
        else:
            mold = ref.mold_band(valid_soil.mold_pct)
            if mold is None:
                req.warnings.append(f"Moldinnhold {format_plain(valid_soil.mold_pct)} % dekkes ikke av n_mold_correction.csv.")
            else:
                used.append(("n_mold_correction.csv", mold))
                n += mold.n_adj
                steps.append(Step("N", f"moldinnhold {format_plain(valid_soil.mold_pct)} %", mold.n_adj, n))
    n = _apply_factor(steps, "N", n, age.n_factor, age.stage)
    req.n = _finish(steps, "N", n)

    # 4. P
    p = culture.p_base
    steps.append(Step("P", f"norm {format_number(p)}", 0.0, p, "start"))
    if valid_soil is None:
        steps.append(Step("P", "ingen jordkorreksjon (mangler gyldig jordprøve)", 0.0, p, "note"))
    elif valid_soil.p_al is None:
        req.warnings.append("Jordprøven mangler P-AL – ingen P-korreksjon.")
        steps.append(Step("P", "ingen jordkorreksjon (P-AL mangler)", 0.0, p, "note"))
    else:
        band = ref.p_al_band(valid_soil.p_al)
        if band is None:
            req.warnings.append(f"P-AL {format_plain(valid_soil.p_al)} dekkes ikke av p_al_correction.csv.")
        else:
            used.append(("p_al_correction.csv", band))
            new_p = p * (1 + band.pct / 100)
            label = (f"P-AL {_soil_value(valid_soil.p_al)} ({_band_text(band.pal_min, band.pal_max, band is ref.p_al_correction[-1])})"
                     f" {format_signed(band.pct, 0, ' %')}")
            steps.append(Step("P", label, new_p - p, new_p, "pct"))
            p = new_p
    p = _apply_factor(steps, "P", p, age.p_factor, age.stage)
    req.p = _finish(steps, "P", p)

    # 5. K
    k = culture.k_base
    steps.append(Step("K", f"norm {format_number(k)}", 0.0, k, "start"))
    if valid_soil is None:
        steps.append(Step("K", "ingen jordkorreksjon (mangler gyldig jordprøve)", 0.0, k, "note"))
    elif valid_soil.k_al is None:
        req.warnings.append("Jordprøven mangler K-AL – ingen K-korreksjon.")
        steps.append(Step("K", "ingen jordkorreksjon (K-AL mangler)", 0.0, k, "note"))
    else:
        cell = ref.k_correction_for(valid_soil.k_al, valid_soil.k_hno3)
        if cell is None:
            req.warnings.append(f"K-AL {format_plain(valid_soil.k_al)} dekkes ikke av k_correction.csv.")
        else:
            used.append(("k_correction.csv", cell))
            if valid_soil.k_hno3 is None:
                hno3_text = f"K-HNO₃ mangler (antatt {_band_text(cell.khno3_min, cell.khno3_max)})"
                req.warnings.append(f"K-HNO₃ mangler – bruker raden for {_band_text(cell.khno3_min, cell.khno3_max)} "
                                    "i k_correction.csv.")
            else:
                hno3_text = f"K-HNO₃ {_soil_value(valid_soil.k_hno3)}"
            new_k = k * (1 + cell.pct / 100)
            label = f"K-AL {_soil_value(valid_soil.k_al)}, {hno3_text} {format_signed(cell.pct, 0, ' %')}"
            steps.append(Step("K", label, new_k - k, new_k, "pct"))
            k = new_k
    k = _apply_factor(steps, "K", k, age.k_factor, age.stage)
    req.k = _finish(steps, "K", k)

    # 7. Soil-status notes (no effect on the numbers)
    if valid_soil is not None:
        req.soil_notes = soil_notes(
            {"ph": valid_soil.ph, "p_al": valid_soil.p_al, "k_al": valid_soil.k_al,
             "mg_al": valid_soil.mg_al, "ca_al": valid_soil.ca_al},
            ref.soil_optimum,
        )

    # 8. Unverified reference rows
    if req.unverified_rows:
        req.warnings.append(f"{UNVERIFIED_PREFIX}: {describe_rows(req.unverified_rows)}.")
    return req


def describe_rows(rows: list[tuple[str, object]]) -> str:
    """'cultures.csv (paere); age_factors.csv (bærende, planteår)' – grouped by file, in first-seen order."""
    by_file: dict[str, list[str]] = {}
    for file, row in rows:
        keys = by_file.setdefault(file, [])
        if _row_key(row) not in keys:
            keys.append(_row_key(row))
    return "; ".join(f"{file} ({', '.join(keys)})" for file, keys in by_file.items())


def rationale_text(steps: list[Step], finals: dict[str, float | None]) -> str:
    """'N: norm 4,0 + avling 3500 kg (+3,0) + nyvekst 20–40 cm (±0) = 7,0 kg/daa. P: …'"""
    parts = []
    for nutrient in ("N", "P", "K"):
        nutrient_steps = [s for s in steps if s.nutrient == nutrient]
        if not nutrient_steps or finals.get(nutrient) is None:
            continue
        text = f"{nutrient}: "
        for s in nutrient_steps:
            if s.kind == "start":
                text += s.label_nb
            elif s.kind == "add":
                text += f" + {s.label_nb} ({format_signed(s.delta)})"
            elif s.kind == "factor":
                text += f" × {s.label_nb}"
            else:  # pct, clamp, note
                text += f", {s.label_nb}"
        parts.append(f"{text} = {format_number(finals[nutrient])} kg/daa.")
    return " ".join(parts)


def _apply_factor(steps: list[Step], nutrient: str, value: float, factor: float, stage: str) -> float:
    if factor == 1:
        return value
    new = value * factor
    steps.append(Step(nutrient, f"{format_plain(factor)} ({stage})", new - value, new, "factor"))
    return new


def _finish(steps: list[Step], nutrient: str, value: float) -> float:
    if value < 0:
        steps.append(Step(nutrient, "satt til 0 (kan ikke være negativ)", -value, 0.0, "clamp"))
        value = 0.0
    return round_half_up(value, 1)


def _band_text(lo: float, hi: float, open_ended: bool = False) -> str:
    return f"≥{format_plain(lo)}" if open_ended else f"{format_plain(lo)}–{format_plain(hi)}"


def _soil_value(value: float) -> str:
    return format_plain(round_half_up(value, 1))


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _row_key(row: object) -> str:
    for attr in ("code", "stage", "growth_class", "parameter"):
        if hasattr(row, attr):
            return str(getattr(row, attr))
    if hasattr(row, "pal_min"):
        return f"P-AL {_band_text(row.pal_min, row.pal_max)}"
    if hasattr(row, "kal_min"):
        return f"K-AL {_band_text(row.kal_min, row.kal_max)} / K-HNO₃ {_band_text(row.khno3_min, row.khno3_max)}"
    if hasattr(row, "mold_min"):
        return f"mold {_band_text(row.mold_min, row.mold_max)}"
    if hasattr(row, "region"):
        return f"{row.region} {row.year_from}–{row.year_to}"
    return "?"
