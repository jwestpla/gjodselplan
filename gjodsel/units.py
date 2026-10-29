"""Oxide/element conversion and Norwegian number parsing/formatting.

Internally all nutrients are pure elements (N, P, K). Oxide forms only
appear at input and display.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

# Molar-mass ratios (chemistry constants, not agronomic norms).
P2O5_TO_P = 0.4364
K2O_TO_K = 0.8301

# Whitespace that may appear as a thousands separator in pasted Norwegian numbers.
_SPACES = "    "


def p2o5_to_p(value: float) -> float:
    return value * P2O5_TO_P


def p_to_p2o5(value: float) -> float:
    return value / P2O5_TO_P


def k2o_to_k(value: float) -> float:
    return value * K2O_TO_K


def k_to_k2o(value: float) -> float:
    return value / K2O_TO_K


def parse_number(text: str | float | int | None) -> float | None:
    """Parse a number written with decimal comma or point.

    Returns None for empty input. Raises ValueError for anything that is not
    a plain finite number (e.g. "abc", "1,2,3", "1.234,5", "nan").
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        cleaned = str(text).strip()
        for ch in _SPACES:
            cleaned = cleaned.replace(ch, "")
        if not cleaned:
            return None
        cleaned = cleaned.replace("−", "-").replace(",", ".")
        if cleaned.count(".") > 1:
            raise ValueError(f"Ugyldig tall: {text!r}")
        value = float(cleaned)
    if not math.isfinite(value):
        raise ValueError(f"Ugyldig tall: {text!r}")
    return value


def round_half_up(value: float, decimals: int = 1) -> float:
    """Round like a person would (0.25 -> 0.3), not banker's rounding."""
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def format_plain(value: float | int | None) -> str:
    """Full precision with decimal comma, for editing and raw data: 4.0 -> '4', 24.8748 -> '24,8748'."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value).replace(".", ",") if isinstance(value, float) else str(value)


def format_number(value: float | None, decimals: int = 1, empty: str = "") -> str:
    """Format with decimal comma, e.g. 10.8 -> '10,8'. None -> `empty`."""
    if value is None:
        return empty
    rounded = round_half_up(value, decimals)
    if rounded == 0:
        rounded = 0.0  # avoid "-0,0"
    return f"{rounded:.{decimals}f}".replace(".", ",")


def format_signed(value: float, decimals: int = 1, suffix: str = "") -> str:
    """'+3,0', '−0,5' (Unicode minus) or '±0' when it rounds to zero."""
    rounded = round_half_up(value, decimals)
    if rounded == 0:
        return f"±0{suffix}"
    sign = "+" if rounded > 0 else "−"
    return f"{sign}{format_number(abs(rounded), decimals)}{suffix}"


_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y")


def parse_date(text: str | None) -> date | None:
    """Accept ISO (2025-04-01) or Norwegian (01.04.2025, 1.4.25) dates. Empty -> None."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Ugyldig dato: {text!r}")


def format_date(iso: str | None) -> str:
    """'2025-04-01' -> '01.04.2025'. Empty for None."""
    if not iso:
        return ""
    return date.fromisoformat(iso[:10]).strftime("%d.%m.%Y")
