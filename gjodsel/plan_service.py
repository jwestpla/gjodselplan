"""Plan orchestration: combines repo (database), calc and planning.

The UI and PDF export call these functions; they never compute plan numbers
themselves. Finished plans are rendered from their frozen snapshot.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace

from . import repo
from .calc import Requirement, calculate_requirement, describe_rows
from .db import now_iso
from .models import Application, Field, FieldSeason, Plan, Settings, SoilSample
from .plan_view import AppLine, FieldRow, PlanView, SampleLine, StepLine
from .planning import (
    PFieldRow,
    application_supply,
    balance,
    balance_status,
    default_soil_sample,
    field_input,
    legal_status,
    p_check,
    soil_check,
    soil_input,
)
from .reference import RefTables
from .soil import sample_validity
from .units import format_date, format_number
from .validation import ValidationError

UNVERIFIED_WARNING = "Planen bygger på ikke-verifiserte normverdier"
_NPK = ("n", "p", "k")


# --- Creating and editing ---------------------------------------------------------------


def default_season(conn: sqlite3.Connection, ref: RefTables, settings: Settings, f: Field, year: int) -> FieldSeason:
    """New field season: yield from cultures.csv, newest valid linked soil sample, forgrøde = same culture."""
    culture = ref.cultures.get(f.culture_code)
    sample = default_soil_sample(repo.samples_for_field(conn, f.id), year, settings.soil_max_age_years)
    planted_before = f.planting_year is not None and f.planting_year < year
    return FieldSeason(
        field_id=f.id,
        fertilized=True,
        expected_yield_kg_daa=culture.default_yield_kg_daa if culture else None,
        forgrode=culture.name_nb if culture and planted_before else None,
        soil_sample_id=sample.id if sample else None,
    )


def create_plan(conn: sqlite3.Connection, ref: RefTables, settings: Settings, enterprise_id: int,
                year: int) -> Plan:
    if repo.list_plans(conn, enterprise_id, year):
        raise ValidationError(f"Det finnes allerede en plan for {year}.")
    fields = repo.list_fields(conn, enterprise_id, include_inactive=False)
    if not fields:
        raise ValidationError("Foretaket har ingen aktive skifter. Registrer skifter først.")
    seasons = [default_season(conn, ref, settings, f, year) for f in fields]
    plan = repo.create_plan(conn, Plan(enterprise_id=enterprise_id, year=year), seasons)
    recalculate(conn, ref, settings, plan)
    return plan


def add_missing_fields(conn: sqlite3.Connection, ref: RefTables, settings: Settings, plan: Plan) -> int:
    """Add active fields that are not yet in a draft plan. Returns how many were added."""
    present = {s.field_id for s in repo.list_field_seasons(conn, plan.id)}
    new = [default_season(conn, ref, settings, f, plan.year)
           for f in repo.list_fields(conn, plan.enterprise_id, include_inactive=False) if f.id not in present]
    if new:
        repo.add_field_seasons(conn, plan.id, new)
        recalculate(conn, ref, settings, plan)
    return len(new)


def recalculate(conn: sqlite3.Connection, ref: RefTables, settings: Settings, plan: Plan) -> None:
    """Store freshly computed requirements for every field season of a draft plan."""
    if plan.finished:
        return
    fields = {f.id: f for f in repo.list_fields(conn, plan.enterprise_id)}
    samples = {s.id: s for s in repo.list_soil_samples(conn, plan.enterprise_id)}
    for season in repo.list_field_seasons(conn, plan.id):
        req = _requirement(ref, settings, plan, season, fields[season.field_id], samples.get(season.soil_sample_id))
        _store(conn, season, req)


def save_season(conn: sqlite3.Connection, ref: RefTables, settings: Settings, season: FieldSeason) -> FieldSeason:
    saved = repo.save_field_season(conn, season, ref.n_growth_adjustment.keys())
    plan = repo.get_plan(conn, saved.plan_id)
    sample = repo.get_soil_sample(conn, saved.soil_sample_id) if saved.soil_sample_id else None
    _store(conn, saved, _requirement(ref, settings, plan, saved, repo.get_field(conn, saved.field_id), sample))
    return repo.get_field_season(conn, saved.id)


def save_application(conn: sqlite3.Connection, ref: RefTables, app: Application) -> Application:
    return repo.save_application(conn, app, ref.products.keys(), ref.timings.keys())


def template_names(ref: RefTables) -> list[str]:
    return list(dict.fromkeys(r.template for r in ref.templates))


def template_stage(ref: RefTables, name: str) -> str | None:
    return next((r.stage for r in ref.templates if r.template == name), None)


def template_applications(ref: RefTables, name: str) -> list[Application]:
    rows = sorted((r for r in ref.templates if r.template == name), key=lambda r: r.order)
    return [Application(kind="planlagt", product_code=r.product_code, kg_per_daa=r.kg_per_daa,
                        timing_code=r.timing_code, note=f"Mal: {name}") for r in rows]


def apply_template(conn: sqlite3.Connection, ref: RefTables, season_ids: list[int], name: str) -> None:
    """Replace the planned applications of the given field seasons with a template from templates.csv."""
    for season_id in season_ids:
        repo.replace_planned_applications(
            conn, season_id, template_applications(ref, name), ref.products.keys(), ref.timings.keys()
        )


def copy_planned_to_actual(conn: sqlite3.Connection, ref: RefTables, planned_ids: list[int], done_date: str,
                           performed_by: str | None = None) -> int:
    """Pre-fill the journal: one actual application per chosen planned one, with the given date."""
    apps = [a for a in (repo.get_application(conn, i) for i in planned_ids) if a is not None and a.kind == "planlagt"]
    for a in apps:
        repo.save_application(
            conn,
            replace(a, id=None, kind="utfort", date=done_date, performed_by=performed_by,
                    created_at=None, updated_at=None),
            ref.products.keys(), ref.timings.keys(),
        )
    return len(apps)


# --- Lifecycle (PLAN §7: Ferdigstill, Ny versjon, Kopier fra fjorår) --------------------------


def finalize(conn: sqlite3.Connection, ref: RefTables, settings: Settings, plan: Plan) -> Plan:
    """Freeze the plan: store the full report model (incl. reference rows used) and set status 'ferdig'."""
    if plan.finished:
        raise ValidationError(repo.FINISHED_PLAN)
    view = _build_live(conn, ref, settings, plan)
    stamp = now_iso()
    view.status, view.finalized_at, view.generated_at = "ferdig", stamp, stamp
    for row in view.rows:
        row.actual = []  # the journal stays live; it is overlaid when the snapshot is shown
        row.actual_supply = {}
    return repo.finalize_plan(conn, plan.id, view.to_json(), stamp)


def new_version(conn: sqlite3.Connection, ref: RefTables, settings: Settings, plan: Plan, reason: str) -> Plan:
    """Copy a plan to version + 1 as a draft. Journal entries move along so the journal stays complete."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Ny versjon krever en begrunnelse for endringen.")
    latest = repo.latest_plan(conn, plan.enterprise_id, plan.year)
    if latest is None or latest.id != plan.id:
        raise ValidationError("Ny versjon kan bare lages fra siste versjon av planen.")
    seasons = repo.list_field_seasons(conn, plan.id)
    planned = _planned_by_field(conn, plan.id, seasons)
    copies = [replace(s, id=None, plan_id=None, created_at=None, updated_at=None) for s in seasons]
    new = repo.create_plan(
        conn, Plan(enterprise_id=plan.enterprise_id, year=plan.year, version=plan.version + 1, change_reason=reason),
        copies, planned,
    )
    repo.move_actual_applications(conn, plan.id, new.id)
    recalculate(conn, ref, settings, new)
    return new


def copy_from_last_year(conn: sqlite3.Connection, ref: RefTables, settings: Settings, enterprise_id: int,
                        year: int) -> tuple[Plan, list[str]]:
    """New plan for `year` based on the newest finished (else draft) plan of year − 1.

    Season inputs and planned applications are copied; overrides are not (they are
    year-specific). Soil samples are re-chosen and requirements recalculated for the
    new year. Returns the plan and descriptions of fields whose stage changed.
    """
    if repo.list_plans(conn, enterprise_id, year):
        raise ValidationError(f"Det finnes allerede en plan for {year}.")
    previous = repo.list_plans(conn, enterprise_id, year - 1)
    if not previous:
        raise ValidationError(f"Fant ingen plan for {year - 1} å kopiere fra.")
    finished = [p for p in previous if p.finished]
    source = (finished or previous)[-1]

    old_seasons = {s.field_id: s for s in repo.list_field_seasons(conn, source.id)}
    old_planned = _planned_by_field(conn, source.id, list(old_seasons.values()))
    seasons, planned, changed = [], {}, []
    for f in repo.list_fields(conn, enterprise_id, include_inactive=False):
        fresh = default_season(conn, ref, settings, f, year)
        old = old_seasons.get(f.id)
        if old is not None:
            fresh = replace(fresh, fertilized=old.fertilized, expected_yield_kg_daa=old.expected_yield_kg_daa,
                            growth_class=old.growth_class, notes=old.notes)
            planned[f.id] = old_planned.get(f.id, [])
        seasons.append(fresh)
        if f.planting_year is not None:
            before = ref.age_factor(year - 1 - f.planting_year) if year - 1 >= f.planting_year else None
            after = ref.age_factor(year - f.planting_year)
            if before is not None and after is not None and before.stage != after.stage:
                changed.append(f"{f.field_no} {f.name}: {before.stage} → {after.stage}")
    plan = repo.create_plan(conn, Plan(enterprise_id=enterprise_id, year=year), seasons, planned)
    recalculate(conn, ref, settings, plan)
    return plan, changed


def _planned_by_field(conn: sqlite3.Connection, plan_id: int, seasons: list[FieldSeason]) -> dict[int, list[Application]]:
    field_of = {s.id: s.field_id for s in seasons}
    planned: dict[int, list[Application]] = {}
    for a in repo.list_applications(conn, plan_id, "planlagt"):
        planned.setdefault(field_of[a.field_season_id], []).append(
            replace(a, id=None, field_season_id=None, created_at=None, updated_at=None))
    return planned


# --- Building the report model ------------------------------------------------------------------


def build_view(conn: sqlite3.Connection, ref: RefTables, settings: Settings, plan: Plan) -> PlanView:
    """Finished plans come from their snapshot (with the live journal overlaid); drafts are built live."""
    if plan.finished and plan.snapshot_json:
        view = PlanView.from_json(plan.snapshot_json)
        _overlay_actual(conn, ref, view)
        return view
    return _build_live(conn, ref, settings, plan)


def status_summary(conn: sqlite3.Connection, ref: RefTables | None, settings: Settings | None,
                   enterprise_id: int, year: int) -> tuple[str, bool]:
    """(text for the status bar, is_problem)."""
    plan = repo.latest_plan(conn, enterprise_id, year)
    if plan is None or ref is None or settings is None:
        area = sum(f.area_daa for f in repo.list_fields(conn, enterprise_id, include_inactive=False))
        text = f"Ingen plan for {year}" if plan is None else f"Plan {year} v{plan.version}"
        if settings is not None:
            text += f" · aktivt areal {format_number(area)} daa: {legal_status(area, settings).text}"
        return text, False
    view = build_view(conn, ref, settings, plan)
    text = (f"Plan {year} v{plan.version} ({plan.status}) · {format_number(view.fertilized_area)} daa gjødslet · "
            f"{view.legal_text} · {view.p_summary}")
    return text, view.p_ok is False or bool(view.soil_missing)


def _requirement(ref: RefTables, settings: Settings, plan: Plan, season: FieldSeason, f: Field,
                 sample: SoilSample | None) -> Requirement:
    return calculate_requirement(field_input(f, season), soil_input(sample), plan.year, ref, settings)


def _store(conn: sqlite3.Connection, season: FieldSeason, req: Requirement) -> None:
    if (season.req_n, season.req_p, season.req_k, season.rationale) != (req.n, req.p, req.k, req.rationale):
        repo.set_requirement(conn, season.id, req.n, req.p, req.k, req.rationale)


def _app_line(a: Application, area: float, ref: RefTables) -> AppLine:
    product = ref.products.get(a.product_code)
    timing = ref.timings.get(a.timing_code) if a.timing_code else None
    return AppLine(
        id=a.id, kind=a.kind, product_code=a.product_code,
        product_name=product.name if product else f"{a.product_code} (ukjent produkt)",
        kg_per_daa=a.kg_per_daa, total_kg=a.kg_per_daa * area,
        timing_code=a.timing_code, timing_label=timing.label_nb if timing else (a.timing_code or ""),
        timing_sort=timing.sort if timing else 999, season=timing.season if timing else None,
        date=a.date, performed_by=a.performed_by, method=a.method, note=a.note,
        supply=application_supply(product, a.kg_per_daa),
    )


def _sum_lines(lines: list[AppLine]) -> dict[str, float | None]:
    total: dict[str, float | None] = {}
    for line in lines:
        for n, v in line.supply.items():
            total[n] = None if v is None or total.get(n, 0.0) is None else total.get(n, 0.0) + v
    return {n: total.get(n, 0.0) for n in ("n", "p", "k", "mg", "s", "ca")}


def _overlay_actual(conn: sqlite3.Connection, ref: RefTables, view: PlanView) -> None:
    by_season: dict[int, list[Application]] = {}
    for a in repo.list_applications(conn, view.plan_id, "utfort"):
        by_season.setdefault(a.field_season_id, []).append(a)
    for row in view.rows:
        row.actual = [_app_line(a, row.area_daa, ref) for a in by_season.get(row.season_id, [])]
        row.actual_supply = _sum_lines(row.actual)


def _build_live(conn: sqlite3.Connection, ref: RefTables, settings: Settings, plan: Plan) -> PlanView:
    e = repo.get_enterprise(conn, plan.enterprise_id)
    properties = repo.list_properties(conn, e.id)
    prop_map = {p.id: p for p in properties}
    fields = repo.list_fields(conn, e.id)
    field_map = {f.id: f for f in fields}
    order = {f.id: i for i, f in enumerate(fields)}
    samples = repo.list_soil_samples(conn, e.id)
    sample_map = {s.id: s for s in samples}
    seasons = sorted(repo.list_field_seasons(conn, plan.id), key=lambda s: order.get(s.field_id, 0))
    apps: dict[tuple[int, str], list[Application]] = {}
    for a in repo.list_applications(conn, plan.id):
        apps.setdefault((a.field_season_id, a.kind), []).append(a)

    limit_row = ref.p_limit(e.region, plan.year)
    field_limit = limit_row.kg_p_daa * settings.p_skew_factor if limit_row else None
    used: list[tuple[str, object]] = []
    rows: list[FieldRow] = []
    for season in seasons:
        f = field_map[season.field_id]
        sample = sample_map.get(season.soil_sample_id)
        req = _requirement(ref, settings, plan, season, f, sample)
        if not plan.finished:
            _store(conn, season, req)
        used.extend(req.reference_rows)
        planned = sorted((_app_line(a, f.area_daa, ref) for a in apps.get((season.id, "planlagt"), [])),
                         key=lambda line: (line.timing_sort, line.id or 0))
        actual = [_app_line(a, f.area_daa, ref) for a in apps.get((season.id, "utfort"), [])]
        used.extend(("products.csv", ref.products[a.product_code]) for a in apps.get((season.id, "planlagt"), [])
                    if a.product_code in ref.products)
        planned_supply, actual_supply = _sum_lines(planned), _sum_lines(actual)
        computed = {"n": req.n, "p": req.p, "k": req.k}
        override = {n: getattr(season, f"override_{n}") for n in _NPK}
        effective = {n: override[n] if override[n] is not None else computed[n] for n in _NPK}

        warnings = list(req.warnings)
        if not season.fertilized and planned:
            warnings.append("Skiftet er merket som ikke gjødslet, men har planlagt gjødsling.")
        if not f.active:
            warnings.append("Skiftet er deaktivert.")
        growth = ref.n_growth_adjustment.get(season.growth_class or "")
        culture = ref.cultures.get(f.culture_code)
        p_planned = planned_supply["p"]
        rows.append(FieldRow(
            season_id=season.id, field_id=f.id, field_no=f.field_no, name=f.name, culture_code=f.culture_code,
            culture_name=culture.name_nb if culture else f.culture_code, variety=f.variety, rootstock=f.rootstock,
            area_daa=f.area_daa, planting_year=f.planting_year,
            property_label=prop_map[f.property_id].label if f.property_id in prop_map else "",
            active=f.active, stage=req.stage, fertilized=season.fertilized,
            expected_yield_kg_daa=season.expected_yield_kg_daa, growth_class=season.growth_class,
            growth_label=growth.label_nb if growth else None, forgrode=season.forgrode,
            soil_sample_id=season.soil_sample_id, soil_sample_text=_sample_text(sample, plan.year, settings),
            soil_valid=req.soil_valid, req=computed, override=override, override_reason=season.override_reason,
            effective=effective, rationale=req.rationale,
            steps=[StepLine(s.nutrient, s.label_nb, s.delta, s.value_after, s.kind) for s in req.steps],
            warnings=warnings, soil_notes=req.soil_notes, planned=planned, actual=actual,
            planned_supply=planned_supply, actual_supply=actual_supply,
            balance={n: balance(planned_supply[n], effective[n]) for n in _NPK},
            balance_status={n: balance_status(planned_supply[n], effective[n]) for n in _NPK},
            p_over_field_limit=(None if not season.fertilized or field_limit is None or p_planned is None
                                else p_planned > field_limit + 1e-9),
            notes=season.notes,
        ))

    fertilized = [r for r in rows if r.fertilized]
    area = sum(r.area_daa for r in fertilized)
    legal = legal_status(area, settings)
    pc = p_check([PFieldRow(r.label, r.area_daa, r.planned_supply["p"]) for r in fertilized],
                 e.region, plan.year, ref, settings)
    if limit_row is not None:
        used.append(("p_limits.csv", limit_row))
    sc = soil_check([(r.label, r.fertilized, sample_map.get(r.soil_sample_id)) for r in rows], plan.year, settings)

    warnings = list(pc.messages)
    if sc.missing:
        warnings.append(f"Mangler gyldig jordprøve (mangelfull etter § 26): {', '.join(sc.missing)}.")
    if sc.expiring_soon:
        warnings.append(f"Jordprøve utløper etter {plan.year}: {', '.join(sc.expiring_soon)}.")
    for p in properties:
        if not p.map_attachment:
            warnings.append(f"Kartskisse mangler for eiendom {p.label} (påkrevd etter § 26).")
    if any(r.req["n"] is None for r in rows):
        warnings.append("Behovet kunne ikke beregnes for alle skifter – se varsler per skifte.")
    unverified = describe_rows([(file, row) for file, row in used if getattr(row, "unverified", False)])
    if unverified:
        warnings.append(f"{UNVERIFIED_WARNING}: {unverified}.")

    links = repo.sample_field_links(conn, e.id)
    sample_lines = []
    for s in samples:
        v = sample_validity(s.method, s.year, plan.year, settings.soil_max_age_years)
        sample_lines.append(SampleLine(
            id=s.id, sample_date=s.sample_date, label=s.label, lab=s.lab, lab_report_no=s.lab_report_no,
            method=s.method,
            values={k: getattr(s, k) for k in ("ph", "p_al", "k_al", "mg_al", "ca_al", "k_hno3", "mold_pct")},
            lat=s.lat, lon=s.lon, age=v.age, valid=v.valid,
            validity_text=("utløper snart" if v.expires_soon else "gyldig") if v.valid else v.reason,
            field_nos=[field_map[fid].field_no for fid in links.get(s.id, []) if fid in field_map],
        ))

    advisor = {k: v for k, v in repo.get_settings(conn).items() if k.startswith("advisor_")}
    return PlanView(
        plan_id=plan.id, year=plan.year, version=plan.version, status=plan.status,
        change_reason=plan.change_reason, finalized_at=plan.finalized_at, created_at=plan.created_at,
        enterprise={k: v for k, v in asdict(e).items() if k not in ("created_at", "updated_at")},
        properties=[{**{k: v for k, v in asdict(p).items() if k not in ("created_at", "updated_at")},
                     "label": p.label, "matrikkel": p.matrikkel} for p in properties],
        advisor=advisor, rows=rows, samples=sample_lines,
        soil_optimum={k: {"min": o.min, "max": o.max, "source": o.source} for k, o in ref.soil_optimum.items()},
        fertilized_area=area, plan_required=legal.plan_required, legal_text=legal.text,
        p_limit=pc.limit, p_field_limit=pc.field_limit, p_average=pc.average, p_ok=pc.ok, p_summary=pc.summary,
        p_messages=pc.messages, soil_missing=sc.missing, soil_expiring=sc.expiring_soon,
        warnings=warnings, unverified=unverified, reference_rows=_unique_rows(used), generated_at=now_iso(),
    )


def _sample_text(sample: SoilSample | None, year: int, settings: Settings) -> str:
    if sample is None:
        return "ingen valgt"
    v = sample_validity(sample.method, sample.year, year, settings.soil_max_age_years)
    state = "gyldig" if v.valid else f"ikke gyldig – {v.reason}"
    label = f" {sample.label}" if sample.label else ""
    return f"{format_date(sample.sample_date)}{label} ({state})"


def _unique_rows(used: list[tuple[str, object]]) -> list[dict]:
    seen, out = set(), []
    for file, row in used:
        key = (file, repr(row))
        if key not in seen:
            seen.add(key)
            out.append({"file": file, "row": asdict(row)})
    return out


def recalculate_drafts(conn: sqlite3.Connection, ref: RefTables, settings: Settings) -> list[str]:
    """Recalculate every draft plan (after reference data changed). Returns what changed, per field.

    Finished plans keep their snapshot and are not touched.
    """
    lines = []
    for plan in repo.list_draft_plans(conn):
        before = {s.id: (s.req_n, s.req_p, s.req_k) for s in repo.list_field_seasons(conn, plan.id)}
        recalculate(conn, ref, settings, plan)
        enterprise = repo.get_enterprise(conn, plan.enterprise_id)
        fields = {f.id: f for f in repo.list_fields(conn, plan.enterprise_id)}
        for s in repo.list_field_seasons(conn, plan.id):
            after = (s.req_n, s.req_p, s.req_k)
            if before.get(s.id) != after:
                f = fields[s.field_id]
                parts = [f"{n} {format_number(old, empty='–')} → {format_number(new, empty='–')}"
                         for n, old, new in zip("NPK", before.get(s.id, (None,) * 3), after) if old != new]
                lines.append(f"{enterprise.name} {plan.year} · skifte {f.field_no} {f.name}: {', '.join(parts)}")
    return lines
