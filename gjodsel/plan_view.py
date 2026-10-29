"""Report model of one plan: everything the plan tab, the PDF and the snapshot need.

Plain data only (str, numbers, lists, dicts), so a finished plan can be frozen
as JSON in plan.snapshot_json and shown later exactly as it was approved,
even if fields, soil samples or reference CSVs change afterwards.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields


@dataclass
class AppLine:
    id: int | None
    kind: str
    product_code: str
    product_name: str
    kg_per_daa: float
    total_kg: float
    timing_code: str | None
    timing_label: str
    timing_sort: int
    season: str | None  # 'vaar' | 'sommer_host' (timings.csv)
    date: str | None
    performed_by: str | None
    method: str | None
    note: str | None
    supply: dict[str, float | None]  # kg nutrient per daa


@dataclass
class StepLine:
    nutrient: str
    label_nb: str
    delta: float
    value_after: float
    kind: str


@dataclass
class FieldRow:
    season_id: int
    field_id: int
    field_no: str
    name: str
    culture_code: str
    culture_name: str
    variety: str | None
    rootstock: str | None
    area_daa: float
    planting_year: int | None
    property_label: str
    active: bool
    stage: str | None
    fertilized: bool
    expected_yield_kg_daa: float | None
    growth_class: str | None
    growth_label: str | None
    forgrode: str | None
    soil_sample_id: int | None
    soil_sample_text: str
    soil_valid: bool
    req: dict[str, float | None]
    override: dict[str, float | None]
    override_reason: str | None
    effective: dict[str, float | None]
    rationale: str
    steps: list[StepLine]
    warnings: list[str]
    soil_notes: list[str]
    planned: list[AppLine]
    actual: list[AppLine]
    planned_supply: dict[str, float | None]
    actual_supply: dict[str, float | None]
    balance: dict[str, float | None]
    balance_status: dict[str, str | None]
    p_over_field_limit: bool | None
    notes: str | None

    @property
    def label(self) -> str:
        return f"{self.field_no} {self.name}"


@dataclass
class SampleLine:
    id: int
    sample_date: str
    label: str | None
    lab: str | None
    lab_report_no: str | None
    method: str
    values: dict[str, float | None]  # ph, p_al, k_al, mg_al, ca_al, k_hno3, mold_pct
    lat: float | None
    lon: float | None
    age: int
    valid: bool
    validity_text: str
    field_nos: list[str]


@dataclass
class PlanView:
    plan_id: int
    year: int
    version: int
    status: str
    change_reason: str | None
    finalized_at: str | None
    created_at: str | None
    enterprise: dict
    properties: list[dict]
    advisor: dict
    rows: list[FieldRow]
    samples: list[SampleLine]
    soil_optimum: dict[str, dict]  # parameter -> {min, max}
    fertilized_area: float
    plan_required: bool
    legal_text: str
    p_limit: float | None
    p_field_limit: float | None
    p_average: float | None
    p_ok: bool | None
    p_summary: str
    p_messages: list[str]
    soil_missing: list[str]
    soil_expiring: list[str]
    warnings: list[str]
    unverified: str  # description of unverified reference rows used, '' if none
    reference_rows: list[dict] = field(default_factory=list)  # {"file": ..., "row": {...}}
    generated_at: str = ""

    @property
    def finished(self) -> bool:
        return self.status == "ferdig"

    def row(self, season_id: int) -> FieldRow | None:
        return next((r for r in self.rows if r.season_id == season_id), None)

    def journal_totals(self) -> dict:
        """Actual applications (§ 27): per field kg/daa and kg of N, P, K, and enterprise totals in kg."""
        fields_out, totals = [], {"n": 0.0, "p": 0.0, "k": 0.0}
        for r in self.rows:
            per_daa = {n: sum((line.supply.get(n) or 0) for line in r.actual) for n in totals}
            fields_out.append({"row": r, **{f"{n}_daa": v for n, v in per_daa.items()},
                               **{f"{n}_kg": v * r.area_daa for n, v in per_daa.items()}})
            for n in totals:
                totals[n] += per_daa[n] * r.area_daa
        return {"fields": fields_out, **{f"{n}_kg": v for n, v in totals.items()}}

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, text: str) -> PlanView:
        data = json.loads(text)
        data["rows"] = [_field_row(r) for r in data["rows"]]
        data["samples"] = [SampleLine(**s) for s in data["samples"]]
        return cls(**_known(cls, data))


def _field_row(data: dict) -> FieldRow:
    data = dict(data)
    data["steps"] = [StepLine(**s) for s in data["steps"]]
    data["planned"] = [AppLine(**a) for a in data["planned"]]
    data["actual"] = [AppLine(**a) for a in data["actual"]]
    return FieldRow(**_known(FieldRow, data))


def _known(cls, data: dict) -> dict:
    """Ignore keys a newer/older snapshot may have that this version doesn't know."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}
