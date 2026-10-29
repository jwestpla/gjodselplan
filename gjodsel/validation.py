"""Validation of Norwegian identifiers, contact details and master data.

Error messages are Norwegian because they are shown to the advisor.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from typing import Iterable

from .models import (
    APPLICATION_KINDS, COVER_TYPES, IRRIGATION_TYPES, SOIL_METHODS, TENURES, Application, Enterprise, Field,
    FieldSeason, Property, SoilSample,
)

_ORG_NR_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ValidationError(Exception):
    """A refusal the advisor should see; `errors` are Norwegian messages."""

    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("\n".join(self.errors))


def digits_only(value: str | None) -> str:
    """Strip spaces and other separators people type in numbers."""
    return re.sub(r"[\s.\-]", "", value or "")


def is_valid_org_nr(value: str | None) -> bool:
    """Brønnøysund organisation number: 9 digits, mod11 check digit."""
    digits = digits_only(value)
    if not re.fullmatch(r"\d{9}", digits):
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], _ORG_NR_WEIGHTS))
    check = 11 - total % 11
    if check == 11:
        check = 0
    if check == 10:
        return False
    return check == int(digits[8])


def is_valid_postcode(value: str | None) -> bool:
    return bool(re.fullmatch(r"\d{4}", (value or "").strip()))


def normalize_phone(value: str | None) -> str | None:
    """Return the 8-digit national number, or None if invalid.

    Accepts spaces and a +47 / 0047 country prefix.
    """
    digits = digits_only(value)
    if digits.startswith("+47"):
        digits = digits[3:]
    elif digits.startswith("0047"):
        digits = digits[4:]
    return digits if re.fullmatch(r"\d{8}", digits) else None


def is_valid_phone(value: str | None) -> bool:
    return normalize_phone(value) is not None


def is_valid_email(value: str | None) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def is_valid_kommunenr(value: str | None) -> bool:
    """Four digits. The county list changes with reforms, so it is not checked."""
    digits = (value or "").strip()
    return bool(re.fullmatch(r"\d{4}", digits)) and digits != "0000"


# --- Master data ----------------------------------------------------------------


def clean_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def clean_enterprise(e: Enterprise) -> Enterprise:
    phone = clean_text(e.phone)
    return replace(
        e,
        name=(e.name or "").strip(),
        org_nr=digits_only(e.org_nr) or None,
        contact_first_name=clean_text(e.contact_first_name),
        contact_last_name=clean_text(e.contact_last_name),
        street=clean_text(e.street),
        postcode=clean_text(e.postcode),
        place=clean_text(e.place),
        # Keep an invalid number as typed so the error message can show it.
        phone=normalize_phone(phone) or phone,
        email=clean_text(e.email),
        region=(e.region or "").strip(),
        notes=clean_text(e.notes),
    )


def enterprise_errors(e: Enterprise, regions: Iterable[str] | None = None) -> list[str]:
    errors = []
    if not e.name:
        errors.append("Foretaksnavn må fylles ut.")
    if e.org_nr and not is_valid_org_nr(e.org_nr):
        errors.append(f"Org.nr. «{e.org_nr}» er ugyldig (9 siffer med gyldig kontrollsiffer).")
    if e.postcode and not is_valid_postcode(e.postcode):
        errors.append(f"Postnummer «{e.postcode}» må ha 4 siffer.")
    if e.phone and not is_valid_phone(e.phone):
        errors.append(f"Telefonnummer «{e.phone}» må ha 8 siffer (eventuelt med +47 foran).")
    if e.email and not is_valid_email(e.email):
        errors.append(f"E-postadressen «{e.email}» ser ikke gyldig ut.")
    if not e.region:
        errors.append("Region må velges.")
    elif regions is not None and e.region not in set(regions):
        errors.append(f"Region «{e.region}» finnes ikke i p_limits.csv.")
    return errors


def clean_property(p: Property) -> Property:
    return replace(
        p,
        farm_name=clean_text(p.farm_name),
        kommunenr=(p.kommunenr or "").strip(),
        tenure=(p.tenure or "").strip(),
        notes=clean_text(p.notes),
    )


def property_errors(p: Property) -> list[str]:
    errors = []
    if not is_valid_kommunenr(p.kommunenr):
        errors.append("Kommunenummer må ha 4 siffer.")
    for value, label in ((p.gnr, "Gårdsnummer (gnr)"), (p.bnr, "Bruksnummer (bnr)")):
        if not isinstance(value, int) or value < 1:
            errors.append(f"{label} må være et positivt heltall.")
    for value, label in ((p.fnr, "Festenummer (fnr)"), (p.snr, "Seksjonsnummer (snr)")):
        if not isinstance(value, int) or value < 0:
            errors.append(f"{label} må være 0 eller et positivt heltall.")
    if p.tenure not in TENURES:
        errors.append("Eierform må være «eid» eller «leid».")
    return errors


def clean_field(f: Field) -> Field:
    return replace(
        f,
        field_no=(f.field_no or "").strip(),
        name=(f.name or "").strip(),
        culture_code=(f.culture_code or "").strip(),
        variety=clean_text(f.variety),
        rootstock=clean_text(f.rootstock),
        polygon_geojson=clean_text(f.polygon_geojson),
        notes=clean_text(f.notes),
    )


def field_errors(f: Field, culture_codes: Iterable[str] | None = None) -> list[str]:
    errors = []
    if not f.field_no:
        errors.append("Skiftenummer må fylles ut.")
    if not f.name:
        errors.append("Skiftenavn må fylles ut.")
    if f.property_id is None:
        errors.append("Eiendom må velges.")
    if f.area_daa is None or f.area_daa <= 0:
        errors.append("Areal må være større enn 0 daa.")
    if not f.culture_code:
        errors.append("Kultur må velges.")
    elif culture_codes is not None and f.culture_code not in set(culture_codes):
        errors.append(f"Kultur «{f.culture_code}» finnes ikke i cultures.csv.")
    if f.planting_year is not None and not 1900 <= f.planting_year <= 2100:
        errors.append("Planteår må være mellom 1900 og 2100.")
    for value, label in ((f.row_spacing_m, "Radavstand"), (f.plant_spacing_m, "Planteavstand")):
        if value is not None and value <= 0:
            errors.append(f"{label} må være større enn 0 m.")
    errors.extend(_coordinate_errors(f.lat, f.lon))
    if f.irrigation not in IRRIGATION_TYPES:
        errors.append(f"Vanning må være en av: {', '.join(IRRIGATION_TYPES)}.")
    if f.cover not in COVER_TYPES:
        errors.append(f"Dekke må være en av: {', '.join(COVER_TYPES)}.")
    return errors


def clean_soil_sample(s: SoilSample) -> SoilSample:
    return replace(
        s,
        label=clean_text(s.label),
        lab=clean_text(s.lab),
        lab_report_no=clean_text(s.lab_report_no),
        sample_date=(s.sample_date or "").strip(),
        soil_type=clean_text(s.soil_type),
        clay_class=clean_text(s.clay_class),
        method=(s.method or "").strip(),
        notes=clean_text(s.notes),
    )


def soil_sample_errors(s: SoilSample, today: date | None = None) -> list[str]:
    errors = []
    try:
        sampled = date.fromisoformat(s.sample_date)
    except ValueError:
        errors.append("Prøvedato mangler eller er ugyldig.")
    else:
        if sampled > (today or date.today()):
            errors.append("Prøvedato kan ikke være fram i tid.")
    if s.method not in SOIL_METHODS:
        errors.append("Metode må være «AL» eller «annet».")
    for value, label in ((s.depth_from_cm, "Dybde fra"), (s.depth_to_cm, "Dybde til")):
        if value is not None and value < 0:
            errors.append(f"{label} kan ikke være negativ.")
    if s.depth_from_cm is not None and s.depth_to_cm is not None and s.depth_to_cm <= s.depth_from_cm:
        errors.append("Dybde til må være større enn dybde fra.")
    errors.extend(_coordinate_errors(s.lat, s.lon))
    if s.ph is not None and not 0 <= s.ph <= 14:
        errors.append("pH må være mellom 0 og 14.")
    for value, label in ((s.p_al, "P-AL"), (s.k_al, "K-AL"), (s.mg_al, "Mg-AL"), (s.ca_al, "Ca-AL"),
                         (s.k_hno3, "K-HNO₃")):
        if value is not None and value < 0:
            errors.append(f"{label} kan ikke være negativ.")
    if s.mold_pct is not None and not 0 <= s.mold_pct <= 100:
        errors.append("Moldinnhold må være mellom 0 og 100 %.")
    return errors


def _coordinate_errors(lat: float | None, lon: float | None) -> list[str]:
    errors = []
    if (lat is None) != (lon is None):
        errors.append("Fyll ut både breddegrad og lengdegrad, eller ingen av dem.")
    if lat is not None and not -90 <= lat <= 90:
        errors.append("Breddegrad må være mellom -90 og 90.")
    if lon is not None and not -180 <= lon <= 180:
        errors.append("Lengdegrad må være mellom -180 og 180.")
    return errors


def clean_field_season(s: FieldSeason) -> FieldSeason:
    return replace(
        s,
        growth_class=clean_text(s.growth_class),
        forgrode=clean_text(s.forgrode),
        override_reason=clean_text(s.override_reason),
        notes=clean_text(s.notes),
    )


def field_season_errors(s: FieldSeason, growth_classes: Iterable[str] | None = None) -> list[str]:
    errors = []
    if s.expected_yield_kg_daa is not None and s.expected_yield_kg_daa < 0:
        errors.append("Forventet avling kan ikke være negativ.")
    overrides = (s.override_n, s.override_p, s.override_k)
    if any(v is not None and v < 0 for v in overrides):
        errors.append("Overstyrt behov kan ikke være negativt.")
    if any(v is not None for v in overrides) and not s.override_reason:
        errors.append("Begrunnelse må fylles ut når behovet overstyres.")
    if s.growth_class and growth_classes is not None and s.growth_class not in set(growth_classes):
        errors.append(f"Nyvekstklasse «{s.growth_class}» finnes ikke i n_growth_adjustment.csv.")
    return errors


def clean_application(a: Application) -> Application:
    return replace(
        a,
        product_code=(a.product_code or "").strip(),
        timing_code=clean_text(a.timing_code),
        date=clean_text(a.date),
        performed_by=clean_text(a.performed_by),
        method=clean_text(a.method),
        note=clean_text(a.note),
    )


def application_errors(a: Application, product_codes: Iterable[str] | None = None,
                       timing_codes: Iterable[str] | None = None) -> list[str]:
    errors = []
    if a.kind not in APPLICATION_KINDS:
        errors.append("Ugyldig type gjødsling.")
    if not a.product_code:
        errors.append("Produkt må velges.")
    elif product_codes is not None and a.product_code not in set(product_codes):
        errors.append(f"Produktet «{a.product_code}» finnes ikke i products.csv.")
    if a.kg_per_daa is None or a.kg_per_daa < 0:
        errors.append("Mengde (kg/daa) må fylles ut og kan ikke være negativ.")
    if a.timing_code and timing_codes is not None and a.timing_code not in set(timing_codes):
        errors.append(f"Tidspunkt «{a.timing_code}» finnes ikke i timings.csv.")
    if a.kind == "utfort":
        try:
            done = date.fromisoformat(a.date or "")
        except ValueError:
            errors.append("Dato må fylles ut for utført gjødsling.")
        else:
            if done > date.today():
                errors.append("Dato for utført gjødsling kan ikke være fram i tid.")
    return errors
