"""Dataclasses for client data stored in SQLite.

Field names match the database columns, so repo.py can map rows generically.
"""

from __future__ import annotations

from dataclasses import dataclass

TENURES = ("eid", "leid")
IRRIGATION_TYPES = ("ingen", "drypp", "gjødselvanning")
COVER_TYPES = ("ingen", "tunnel", "regntak")


@dataclass
class Enterprise:
    name: str = ""
    org_nr: str | None = None
    contact_first_name: str | None = None
    contact_last_name: str | None = None
    street: str | None = None
    postcode: str | None = None
    place: str | None = None
    phone: str | None = None
    email: str | None = None
    region: str = "standard"
    notes: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def contact_name(self) -> str:
        return " ".join(p for p in (self.contact_first_name, self.contact_last_name) if p)


@dataclass
class Property:
    enterprise_id: int | None = None
    farm_name: str | None = None
    kommunenr: str = ""
    gnr: int | None = None
    bnr: int | None = None
    fnr: int = 0
    snr: int = 0
    tenure: str = "eid"
    map_attachment: str | None = None
    notes: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def matrikkel(self) -> str:
        """Conventional display form: 4640-12/3, 4640-12/3/1 or 4640-12/3/0/2."""
        text = f"{self.kommunenr}-{self.gnr}/{self.bnr}"
        if self.fnr or self.snr:
            text += f"/{self.fnr}"
        if self.snr:
            text += f"/{self.snr}"
        return text

    @property
    def label(self) -> str:
        return f"{self.farm_name} ({self.matrikkel})" if self.farm_name else self.matrikkel


@dataclass
class Field:
    property_id: int | None = None
    field_no: str = ""
    name: str = ""
    area_daa: float | None = None
    culture_code: str = ""
    variety: str | None = None
    rootstock: str | None = None
    planting_year: int | None = None
    row_spacing_m: float | None = None
    plant_spacing_m: float | None = None
    lat: float | None = None
    lon: float | None = None
    polygon_geojson: str | None = None
    irrigation: str = "ingen"
    cover: str = "ingen"
    active: bool = True
    notes: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def trees_per_daa(self) -> float | None:
        return trees_per_daa(self.row_spacing_m, self.plant_spacing_m)


@dataclass(frozen=True)
class Settings:
    """Typed view of the `setting` table values used by calculations."""

    apply_mold_correction: bool
    p_skew_factor: float
    plan_threshold_daa: float
    soil_max_age_years: int


SOIL_METHODS = ("AL", "annet")


@dataclass
class SoilSample:
    enterprise_id: int | None = None
    label: str | None = None
    lab: str | None = None
    lab_report_no: str | None = None
    sample_date: str = ""  # ISO date
    depth_from_cm: int | None = None
    depth_to_cm: int | None = None
    lat: float | None = None
    lon: float | None = None
    ph: float | None = None
    p_al: float | None = None  # always mg P/100 g
    k_al: float | None = None
    mg_al: float | None = None
    ca_al: float | None = None
    k_hno3: float | None = None
    mold_pct: float | None = None
    soil_type: str | None = None
    clay_class: str | None = None
    method: str = "AL"
    attachment: str | None = None
    notes: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def year(self) -> int:
        return int(self.sample_date[:4])


def trees_per_daa(row_spacing_m: float | None, plant_spacing_m: float | None) -> float | None:
    """1 daa = 1000 m²."""
    if row_spacing_m and plant_spacing_m and row_spacing_m > 0 and plant_spacing_m > 0:
        return 1000 / (row_spacing_m * plant_spacing_m)
    return None


PLAN_STATUSES = ("utkast", "ferdig")
APPLICATION_KINDS = ("planlagt", "utfort")


@dataclass
class Plan:
    enterprise_id: int | None = None
    year: int | None = None
    version: int = 1
    status: str = "utkast"
    change_reason: str | None = None
    finalized_at: str | None = None
    snapshot_json: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def finished(self) -> bool:
        return self.status == "ferdig"


@dataclass
class FieldSeason:
    plan_id: int | None = None
    field_id: int | None = None
    fertilized: bool = True
    expected_yield_kg_daa: float | None = None
    growth_class: str | None = None
    forgrode: str | None = None
    soil_sample_id: int | None = None
    req_n: float | None = None
    req_p: float | None = None
    req_k: float | None = None
    override_n: float | None = None
    override_p: float | None = None
    override_k: float | None = None
    override_reason: str | None = None
    rationale: str | None = None
    notes: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def effective(self, nutrient: str) -> float | None:
        """Override if set, otherwise the computed requirement. nutrient: 'n' | 'p' | 'k'."""
        override = getattr(self, f"override_{nutrient}")
        return override if override is not None else getattr(self, f"req_{nutrient}")


@dataclass
class Application:
    field_season_id: int | None = None
    kind: str = "planlagt"
    product_code: str = ""
    kg_per_daa: float | None = None
    timing_code: str | None = None
    date: str | None = None
    performed_by: str | None = None
    method: str | None = None
    note: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
