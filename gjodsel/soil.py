"""Soil sample helpers: unit conversion on entry, validity and optimum status.

Pure functions (no DB, no UI), shared by the soil tab and calc.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from .reference import SoilOptimum
from .units import format_plain, p2o5_to_p, round_half_up

P_AL_UNIT_P = "mg P/100 g"
P_AL_UNIT_P2O5 = "mg P₂O₅/100 g"
P_AL_UNITS = (P_AL_UNIT_P, P_AL_UNIT_P2O5)

# soil_sample column -> display label, in report order
SOIL_PARAMETERS = {"ph": "pH", "p_al": "P-AL", "k_al": "K-AL", "mg_al": "Mg-AL", "ca_al": "Ca-AL"}


def p_al_to_element(value: float | None, unit: str) -> float | None:
    """Convert an entered P-AL value to mg P/100 g (stored form).

    Oxide values are rounded to 0.1 after conversion: 57 mg P₂O₅ -> 24.9 mg P.
    """
    if value is None:
        return None
    if unit == P_AL_UNIT_P:
        return value
    if unit == P_AL_UNIT_P2O5:
        return round_half_up(p2o5_to_p(value), 1)
    raise ValueError(f"Ukjent P-AL-enhet: {unit}")


@dataclass(frozen=True)
class SampleValidity:
    valid: bool
    age: int
    expires_soon: bool  # still valid this year, too old next year
    reason: str  # Norwegian; empty when valid


def sample_validity(method: str, sample_year: int, plan_year: int, max_age_years: int) -> SampleValidity:
    """A sample counts for a plan year if it is an AL analysis and at most max_age_years old."""
    age = plan_year - sample_year
    if method != "AL":
        return SampleValidity(False, age, False, "ikke AL-metode")
    if age < 0:
        return SampleValidity(False, age, False, "tatt etter planåret")
    if age > max_age_years:
        return SampleValidity(False, age, False, f"utløpt ({age} år gammel, maks {max_age_years})")
    return SampleValidity(True, age, age == max_age_years, "")


def classify(value: float | None, optimum: SoilOptimum | None) -> str | None:
    """'lav' / 'ok' / 'høy' against the optimum range (limits inclusive), None if unknown."""
    if value is None or optimum is None:
        return None
    if value < optimum.min:
        return "lav"
    if value > optimum.max:
        return "høy"
    return "ok"


def soil_notes(values: dict[str, float | None], optimum: dict[str, SoilOptimum]) -> list[str]:
    """Text notes for values outside the optimum, e.g. 'pH 5,2 under optimum 5,5–6,5 – vurder kalking'."""
    notes = []
    for key, label in SOIL_PARAMETERS.items():
        value = values.get(key)
        opt = optimum.get(key)
        status = classify(value, opt)
        if status in (None, "ok"):
            continue
        where = "under" if status == "lav" else "over"
        note = f"{label} {_fmt(value)} {where} optimum {_fmt(opt.min)}–{_fmt(opt.max)}"
        if key == "ph" and status == "lav":
            note += " – vurder kalking"
        notes.append(note)
    return notes


def _fmt(value: float) -> str:
    return format_plain(round_half_up(value, 1))
