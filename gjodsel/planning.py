"""Plan-level calculations (PLAN §6): supply, balances, P check, legal status, soil checks.

Pure functions over model dataclasses; loading from the database happens in
the callers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .calc import FieldInput, SoilInput
from .models import Application, Field, FieldSeason, Settings, SoilSample
from .reference import PLimit, Product, RefTables
from .soil import sample_validity
from .units import format_number

NUTRIENTS = ("n", "p", "k", "mg", "s", "ca")
# Balance colouring (PLAN §7): within ±10 % of the requirement counts as on target.
BALANCE_TOLERANCE = 0.10

Supply = dict[str, float | None]  # nutrient -> kg/daa; None = unknown (product content not given)


# --- Inputs for calc.py -------------------------------------------------------------


def field_input(f: Field, season: FieldSeason) -> FieldInput:
    return FieldInput(
        area_daa=f.area_daa,
        culture_code=f.culture_code,
        planting_year=f.planting_year,
        expected_yield_kg_daa=season.expected_yield_kg_daa,
        growth_class=season.growth_class,
    )


def soil_input(sample: SoilSample | None) -> SoilInput | None:
    if sample is None:
        return None
    return SoilInput(
        sample_date=sample.sample_date, method=sample.method, p_al=sample.p_al, k_al=sample.k_al,
        k_hno3=sample.k_hno3, mold_pct=sample.mold_pct, ph=sample.ph, mg_al=sample.mg_al, ca_al=sample.ca_al,
    )


def default_soil_sample(samples: Iterable[SoilSample], plan_year: int, max_age_years: int) -> SoilSample | None:
    """Newest sample that is valid for the plan year (for preselection in the plan tab)."""
    valid = [s for s in samples if sample_validity(s.method, s.year, plan_year, max_age_years).valid]
    return max(valid, key=lambda s: (s.sample_date, s.id or 0), default=None)


# --- Supply and balance -----------------------------------------------------------------


def application_supply(product: Product | None, kg_per_daa: float | None) -> Supply:
    """kg nutrient per daa from one application: kg_per_daa × pct / 100."""
    if product is None or kg_per_daa is None:
        return {n: None for n in NUTRIENTS}
    return {n: (None if getattr(product, n) is None else kg_per_daa * getattr(product, n) / 100)
            for n in NUTRIENTS}


def sum_supply(applications: Iterable[Application], ref: RefTables) -> Supply:
    """Sum over applications. A nutrient becomes None (unknown) if any product lacks its content."""
    total: Supply = {n: 0.0 for n in NUTRIENTS}
    for a in applications:
        supply = application_supply(ref.products.get(a.product_code), a.kg_per_daa)
        for n in NUTRIENTS:
            total[n] = None if total[n] is None or supply[n] is None else total[n] + supply[n]
    return total


def balance(planned: float | None, required: float | None) -> float | None:
    if planned is None or required is None:
        return None
    return planned - required


def balance_status(planned: float | None, required: float | None) -> str | None:
    """'ok' within ±10 %, 'under' (deficit) or 'over' (excess). None if unknown."""
    if planned is None or required is None:
        return None
    if required == 0:
        return "ok" if planned == 0 else "over"
    deviation = (planned - required) / required
    if deviation > BALANCE_TOLERANCE:
        return "over"
    if deviation < -BALANCE_TOLERANCE:
        return "under"
    return "ok"


# --- Enterprise checks ----------------------------------------------------------------------


@dataclass(frozen=True)
class LegalStatus:
    plan_required: bool
    fertilized_area: float
    text: str


def legal_status(fertilized_area: float, settings: Settings) -> LegalStatus:
    threshold = format_number(settings.plan_threshold_daa, 0)
    if fertilized_area > settings.plan_threshold_daa:
        return LegalStatus(True, fertilized_area, f"Gjødslingsplan påkrevd (> {threshold} daa)")
    return LegalStatus(False, fertilized_area,
                       f"Ikke påkrevd (≤ {threshold} daa) – gjødseljournal er likevel påkrevd")


@dataclass(frozen=True)
class PFieldRow:
    label: str
    area_daa: float
    planned_p: float | None  # kg P/daa, all fertilizer combined


@dataclass
class PCheck:
    limit_row: PLimit | None
    field_limit: float | None
    average: float | None
    breaches: list[PFieldRow] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def limit(self) -> float | None:
        return self.limit_row.kg_p_daa if self.limit_row else None

    @property
    def average_ok(self) -> bool | None:
        if self.limit is None or self.average is None:
            return None
        return self.average <= self.limit + 1e-9

    @property
    def ok(self) -> bool | None:
        """True / False, or None when it can't be judged (no limit, or P unknown)."""
        if self.average_ok is None:
            return None
        return self.average_ok and not self.breaches

    @property
    def summary(self) -> str:
        if self.limit is None:
            return "P-sjekk: ingen P-grense funnet"
        if self.average is None:
            return "P-sjekk: planlagt P ukjent"
        state = "OK" if self.ok else "BRUDD"
        return (f"P-sjekk {state}: snitt {format_number(self.average, 2)} av maks {format_number(self.limit)} "
                f"kg P/daa, per skifte maks {format_number(self.field_limit, 2)}")


def p_check(rows: list[PFieldRow], region: str, year: int, ref: RefTables, settings: Settings) -> PCheck:
    """Enterprise average P must be ≤ limit; each field ≤ limit × p_skew_factor. Rows = fertilized fields."""
    limit_row = ref.p_limit(region, year)
    check = PCheck(limit_row=limit_row, field_limit=None, average=None)
    if limit_row is None:
        check.messages.append(f"Ingen P-grense i p_limits.csv for region «{region}» i {year}.")
        return check
    check.field_limit = limit_row.kg_p_daa * settings.p_skew_factor
    area = sum(r.area_daa for r in rows)
    if any(r.planned_p is None for r in rows):
        check.messages.append("Planlagt P er ukjent for minst ett skifte (produkt mangler P-innhold).")
        return check
    if area <= 0:
        check.average = 0.0
        return check
    check.average = sum(r.planned_p * r.area_daa for r in rows) / area
    if not check.average_ok:
        check.messages.append(
            f"Gjennomsnittlig P {format_number(check.average, 2)} kg/daa er over grensen "
            f"{format_number(check.limit)} kg/daa for {year}."
        )
    check.breaches = [r for r in rows if r.planned_p > check.field_limit + 1e-9]
    for r in check.breaches:
        check.messages.append(
            f"Skifte {r.label}: {format_number(r.planned_p, 2)} kg P/daa er over maks per skifte "
            f"{format_number(check.field_limit, 2)} (grense × {format_number(settings.p_skew_factor, 2)})."
        )
    return check


@dataclass(frozen=True)
class SoilCheck:
    missing: list[str]        # fertilized fields without a valid AL sample ("mangelfull")
    expiring_soon: list[str]  # fields whose sample expires after this year


def soil_check(rows: list[tuple[str, bool, SoilSample | None]], year: int, settings: Settings) -> SoilCheck:
    """rows: (field label, fertilized, chosen soil sample)."""
    missing, expiring = [], []
    for label, fertilized, sample in rows:
        if not fertilized:
            continue
        if sample is None:
            missing.append(label)
            continue
        v = sample_validity(sample.method, sample.year, year, settings.soil_max_age_years)
        if not v.valid:
            missing.append(label)
        elif v.expires_soon:
            expiring.append(label)
    return SoilCheck(missing, expiring)


def duplicate_field_numbers(fields: Iterable[Field]) -> list[str]:
    counts = Counter(f.field_no.strip().lower() for f in fields)
    return sorted(no for no, n in counts.items() if n > 1)
