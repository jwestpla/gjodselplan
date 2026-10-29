"""All SQL lives here. Every write runs inside a transaction.

Save functions clean and validate their input and raise ValidationError with
Norwegian messages, so the UI cannot store invalid data by accident.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import fields, replace
from typing import Iterable, TypeVar

from .db import now_iso, transaction
from .units import parse_number
from .models import Application, Enterprise, Field, FieldSeason, Plan, Property, Settings, SoilSample
from .validation import (
    ValidationError,
    application_errors,
    clean_application,
    clean_enterprise,
    clean_field,
    clean_field_season,
    clean_property,
    clean_soil_sample,
    enterprise_errors,
    field_errors,
    field_season_errors,
    property_errors,
    soil_sample_errors,
)

T = TypeVar("T")

_META = ("id", "created_at", "updated_at")
_BOOL_COLUMNS = {"active", "fertilized"}


# --- Generic mapping ----------------------------------------------------------------


def _from_row(cls: type[T], row: sqlite3.Row) -> T:
    names = {f.name for f in fields(cls)}
    data = {k: row[k] for k in row.keys() if k in names}
    for k in _BOOL_COLUMNS & data.keys():
        data[k] = bool(data[k])
    return cls(**data)


def _values(obj) -> dict:
    return {f.name: getattr(obj, f.name) for f in fields(obj) if f.name not in _META}


def _insert(conn: sqlite3.Connection, table: str, obj) -> int:
    values = _values(obj)
    columns = ", ".join(values)
    marks = ", ".join(f":{c}" for c in values)
    return conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", values).lastrowid


def _update(conn: sqlite3.Connection, table: str, obj) -> None:
    values = _values(obj)
    sets = ", ".join(f"{c} = :{c}" for c in values)
    values.update(id=obj.id, updated_at=now_iso())
    cur = conn.execute(f"UPDATE {table} SET {sets}, updated_at = :updated_at WHERE id = :id", values)
    if cur.rowcount != 1:
        raise ValidationError(f"Fant ikke raden som skulle oppdateres ({table} id {obj.id}).")


def _count(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> int:
    return conn.execute(sql, tuple(params)).fetchone()[0]


def _natural_key(text: str) -> list:
    """Sort '2' before '10' in field numbers."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


# --- Enterprise -------------------------------------------------------------------


def list_enterprises(conn: sqlite3.Connection, search: str = "") -> list[Enterprise]:
    rows = [_from_row(Enterprise, r) for r in conn.execute("SELECT * FROM enterprise")]
    needle = search.strip().casefold()
    if needle:
        rows = [
            e for e in rows
            if needle in " ".join(filter(None, (e.name, e.org_nr, e.contact_name, e.place))).casefold()
        ]
    return sorted(rows, key=lambda e: e.name.casefold())


def get_enterprise(conn: sqlite3.Connection, enterprise_id: int) -> Enterprise | None:
    row = conn.execute("SELECT * FROM enterprise WHERE id = ?", (enterprise_id,)).fetchone()
    return _from_row(Enterprise, row) if row else None


def save_enterprise(conn: sqlite3.Connection, e: Enterprise,
                    regions: Iterable[str] | None = None) -> Enterprise:
    e = clean_enterprise(e)
    errors = enterprise_errors(e, regions)
    if errors:
        raise ValidationError(errors)
    with transaction(conn):
        if e.org_nr and _count(
            conn, "SELECT COUNT(*) FROM enterprise WHERE org_nr = ? AND id IS NOT ?", (e.org_nr, e.id)
        ):
            raise ValidationError(f"Et annet foretak er allerede registrert med org.nr. {e.org_nr}.")
        if e.id is None:
            e.id = _insert(conn, "enterprise", e)
        else:
            _update(conn, "enterprise", e)
    return get_enterprise(conn, e.id)


def enterprise_dependents(conn: sqlite3.Connection, enterprise_id: int) -> dict[str, int]:
    """Counts of rows that belong to the enterprise (for delete confirmation)."""
    return {
        "properties": _count(conn, "SELECT COUNT(*) FROM property WHERE enterprise_id = ?", (enterprise_id,)),
        "fields": _count(
            conn,
            "SELECT COUNT(*) FROM field f JOIN property p ON p.id = f.property_id WHERE p.enterprise_id = ?",
            (enterprise_id,),
        ),
        "soil_samples": _count(conn, "SELECT COUNT(*) FROM soil_sample WHERE enterprise_id = ?", (enterprise_id,)),
        "plans": _count(conn, "SELECT COUNT(*) FROM plan WHERE enterprise_id = ?", (enterprise_id,)),
    }


def delete_enterprise(conn: sqlite3.Connection, enterprise_id: int) -> None:
    """Delete an enterprise with its properties, fields and soil samples.

    Refused if the enterprise has any plan (plans must be kept for 5 years).
    """
    with transaction(conn):
        if enterprise_dependents(conn, enterprise_id)["plans"]:
            raise ValidationError(
                "Foretaket har gjødslingsplaner og kan ikke slettes. "
                "Planer og journaler skal oppbevares i 5 år."
            )
        conn.execute(
            "DELETE FROM field WHERE property_id IN (SELECT id FROM property WHERE enterprise_id = ?)",
            (enterprise_id,),
        )
        conn.execute("DELETE FROM soil_sample WHERE enterprise_id = ?", (enterprise_id,))
        conn.execute("DELETE FROM property WHERE enterprise_id = ?", (enterprise_id,))
        conn.execute("DELETE FROM enterprise WHERE id = ?", (enterprise_id,))


# --- Property -----------------------------------------------------------------------


def list_properties(conn: sqlite3.Connection, enterprise_id: int) -> list[Property]:
    rows = conn.execute(
        "SELECT * FROM property WHERE enterprise_id = ? ORDER BY kommunenr, gnr, bnr, fnr, snr",
        (enterprise_id,),
    )
    return [_from_row(Property, r) for r in rows]


def get_property(conn: sqlite3.Connection, property_id: int) -> Property | None:
    row = conn.execute("SELECT * FROM property WHERE id = ?", (property_id,)).fetchone()
    return _from_row(Property, row) if row else None


def save_property(conn: sqlite3.Connection, p: Property) -> Property:
    p = clean_property(p)
    errors = property_errors(p)
    if p.enterprise_id is None:
        errors.append("Eiendommen må høre til et foretak.")
    if errors:
        raise ValidationError(errors)
    with transaction(conn):
        if _count(
            conn,
            "SELECT COUNT(*) FROM property WHERE enterprise_id = ? AND kommunenr = ? AND gnr = ? "
            "AND bnr = ? AND fnr = ? AND snr = ? AND id IS NOT ?",
            (p.enterprise_id, p.kommunenr, p.gnr, p.bnr, p.fnr, p.snr, p.id),
        ):
            raise ValidationError(f"Eiendommen {p.matrikkel} er allerede registrert på dette foretaket.")
        if p.id is None:
            p.id = _insert(conn, "property", p)
        else:
            _update(conn, "property", p)
    return get_property(conn, p.id)


def set_property_map(conn: sqlite3.Connection, property_id: int, relative_path: str | None) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE property SET map_attachment = ?, updated_at = ? WHERE id = ?",
            (relative_path, now_iso(), property_id),
        )


def property_field_count(conn: sqlite3.Connection, property_id: int) -> int:
    return _count(conn, "SELECT COUNT(*) FROM field WHERE property_id = ?", (property_id,))


def property_has_plans(conn: sqlite3.Connection, property_id: int) -> bool:
    return bool(_count(
        conn,
        "SELECT COUNT(*) FROM field_season fs JOIN field f ON f.id = fs.field_id WHERE f.property_id = ?",
        (property_id,),
    ))


def delete_property(conn: sqlite3.Connection, property_id: int) -> None:
    """Delete a property and its fields. Refused if any field is used in a plan."""
    with transaction(conn):
        if property_has_plans(conn, property_id):
            raise ValidationError(
                "Eiendommen har skifter som inngår i gjødslingsplaner og kan ikke slettes. "
                "Deaktiver skiftene i stedet."
            )
        conn.execute("DELETE FROM field WHERE property_id = ?", (property_id,))
        conn.execute("DELETE FROM property WHERE id = ?", (property_id,))


# --- Field ----------------------------------------------------------------------------


def list_fields(conn: sqlite3.Connection, enterprise_id: int, include_inactive: bool = True) -> list[Field]:
    sql = (
        "SELECT f.* FROM field f JOIN property p ON p.id = f.property_id WHERE p.enterprise_id = ?"
        + ("" if include_inactive else " AND f.active = 1")
    )
    rows = [_from_row(Field, r) for r in conn.execute(sql, (enterprise_id,))]
    return sorted(rows, key=lambda f: _natural_key(f.field_no))


def get_field(conn: sqlite3.Connection, field_id: int) -> Field | None:
    row = conn.execute("SELECT * FROM field WHERE id = ?", (field_id,)).fetchone()
    return _from_row(Field, row) if row else None


def save_field(conn: sqlite3.Connection, f: Field, culture_codes: Iterable[str] | None = None) -> Field:
    f = clean_field(f)
    errors = field_errors(f, culture_codes)
    if errors:
        raise ValidationError(errors)
    with transaction(conn):
        row = conn.execute("SELECT enterprise_id FROM property WHERE id = ?", (f.property_id,)).fetchone()
        if row is None:
            raise ValidationError("Eiendommen finnes ikke.")
        enterprise_id = row[0]
        if f.id is not None:
            old = conn.execute(
                "SELECT p.enterprise_id FROM field f JOIN property p ON p.id = f.property_id WHERE f.id = ?",
                (f.id,),
            ).fetchone()
            if old is not None and old[0] != enterprise_id:
                raise ValidationError("Et skifte kan ikke flyttes til et annet foretak.")
        if _count(
            conn,
            "SELECT COUNT(*) FROM field f JOIN property p ON p.id = f.property_id "
            "WHERE p.enterprise_id = ? AND lower(trim(f.field_no)) = lower(?) AND f.id IS NOT ?",
            (enterprise_id, f.field_no, f.id),
        ):
            raise ValidationError(f"Skiftenummer «{f.field_no}» finnes allerede på dette foretaket.")
        if f.id is None:
            f.id = _insert(conn, "field", f)
        else:
            _update(conn, "field", f)
    return get_field(conn, f.id)


def field_has_plans(conn: sqlite3.Connection, field_id: int) -> bool:
    return bool(_count(conn, "SELECT COUNT(*) FROM field_season WHERE field_id = ?", (field_id,)))


def set_field_active(conn: sqlite3.Connection, field_id: int, active: bool) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE field SET active = ?, updated_at = ? WHERE id = ?", (int(active), now_iso(), field_id)
        )


def delete_field(conn: sqlite3.Connection, field_id: int) -> None:
    with transaction(conn):
        if field_has_plans(conn, field_id):
            raise ValidationError(
                "Skiftet inngår i en gjødslingsplan og kan ikke slettes. Deaktiver det i stedet."
            )
        conn.execute("DELETE FROM field WHERE id = ?", (field_id,))


# --- Soil samples ------------------------------------------------------------------------


def list_soil_samples(conn: sqlite3.Connection, enterprise_id: int) -> list[SoilSample]:
    rows = conn.execute(
        "SELECT * FROM soil_sample WHERE enterprise_id = ? ORDER BY sample_date DESC, id DESC", (enterprise_id,)
    )
    return [_from_row(SoilSample, r) for r in rows]


def get_soil_sample(conn: sqlite3.Connection, sample_id: int) -> SoilSample | None:
    row = conn.execute("SELECT * FROM soil_sample WHERE id = ?", (sample_id,)).fetchone()
    return _from_row(SoilSample, row) if row else None


def save_soil_sample(conn: sqlite3.Connection, s: SoilSample,
                     field_ids: Iterable[int] | None = None) -> SoilSample:
    """Insert or update a sample. If field_ids is given, it replaces the linked fields."""
    s = clean_soil_sample(s)
    errors = soil_sample_errors(s)
    if s.enterprise_id is None:
        errors.append("Jordprøven må høre til et foretak.")
    if errors:
        raise ValidationError(errors)
    with transaction(conn):
        if s.id is None:
            s.id = _insert(conn, "soil_sample", s)
        else:
            _update(conn, "soil_sample", s)
        if field_ids is not None:
            field_ids = sorted(set(field_ids))
            own = {r[0] for r in conn.execute(
                "SELECT f.id FROM field f JOIN property p ON p.id = f.property_id WHERE p.enterprise_id = ?",
                (s.enterprise_id,),
            )}
            if not set(field_ids) <= own:
                raise ValidationError("Jordprøven kan bare knyttes til skifter på samme foretak.")
            conn.execute("DELETE FROM field_soil_sample WHERE soil_sample_id = ?", (s.id,))
            conn.executemany(
                "INSERT INTO field_soil_sample (field_id, soil_sample_id) VALUES (?, ?)",
                [(fid, s.id) for fid in field_ids],
            )
    return get_soil_sample(conn, s.id)


def sample_field_links(conn: sqlite3.Connection, enterprise_id: int) -> dict[int, list[int]]:
    """soil_sample_id -> linked field ids, for one enterprise."""
    links: dict[int, list[int]] = {}
    rows = conn.execute(
        "SELECT l.soil_sample_id, l.field_id FROM field_soil_sample l "
        "JOIN soil_sample s ON s.id = l.soil_sample_id WHERE s.enterprise_id = ?",
        (enterprise_id,),
    )
    for sample_id, field_id in rows:
        links.setdefault(sample_id, []).append(field_id)
    return links


def samples_for_field(conn: sqlite3.Connection, field_id: int) -> list[SoilSample]:
    rows = conn.execute(
        "SELECT s.* FROM soil_sample s JOIN field_soil_sample l ON l.soil_sample_id = s.id "
        "WHERE l.field_id = ? ORDER BY s.sample_date DESC, s.id DESC",
        (field_id,),
    )
    return [_from_row(SoilSample, r) for r in rows]


def soil_sample_in_use(conn: sqlite3.Connection, sample_id: int) -> bool:
    return bool(_count(conn, "SELECT COUNT(*) FROM field_season WHERE soil_sample_id = ?", (sample_id,)))


def set_soil_sample_attachment(conn: sqlite3.Connection, sample_id: int, relative_path: str | None) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE soil_sample SET attachment = ?, updated_at = ? WHERE id = ?",
            (relative_path, now_iso(), sample_id),
        )


def delete_soil_sample(conn: sqlite3.Connection, sample_id: int) -> None:
    with transaction(conn):
        if soil_sample_in_use(conn, sample_id):
            raise ValidationError("Jordprøven er brukt i en gjødslingsplan og kan ikke slettes.")
        conn.execute("DELETE FROM soil_sample WHERE id = ?", (sample_id,))


# --- Plans ----------------------------------------------------------------------------------

FINISHED_PLAN = "Planen er ferdigstilt og kan ikke endres. Lag en ny versjon for å gjøre endringer."


def list_plans(conn: sqlite3.Connection, enterprise_id: int, year: int | None = None) -> list[Plan]:
    sql = "SELECT * FROM plan WHERE enterprise_id = ?"
    params: list = [enterprise_id]
    if year is not None:
        sql += " AND year = ?"
        params.append(year)
    return [_from_row(Plan, r) for r in conn.execute(sql + " ORDER BY year, version", params)]


def get_plan(conn: sqlite3.Connection, plan_id: int) -> Plan | None:
    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    return _from_row(Plan, row) if row else None


def latest_plan(conn: sqlite3.Connection, enterprise_id: int, year: int) -> Plan | None:
    plans = list_plans(conn, enterprise_id, year)
    return plans[-1] if plans else None


def create_plan(conn: sqlite3.Connection, plan: Plan, seasons: list[FieldSeason],
                planned: dict[int, list[Application]] | None = None) -> Plan:
    """Insert a plan with its field seasons and (optionally) planned applications keyed by field_id."""
    if plan.version > 1 and not (plan.change_reason or "").strip():
        raise ValidationError("Ny versjon krever en begrunnelse for endringen.")
    with transaction(conn):
        if _count(conn, "SELECT COUNT(*) FROM plan WHERE enterprise_id = ? AND year = ? AND version = ?",
                  (plan.enterprise_id, plan.year, plan.version)):
            raise ValidationError(f"Det finnes allerede en plan for {plan.year}, versjon {plan.version}.")
        plan.id = _insert(conn, "plan", plan)
        for season in seasons:
            season.plan_id = plan.id
            season.id = _insert(conn, "field_season", season)
            for app in (planned or {}).get(season.field_id, []):
                app.field_season_id = season.id
                _insert(conn, "application", app)
    return get_plan(conn, plan.id)


def _require_draft(conn: sqlite3.Connection, plan_id: int) -> None:
    row = conn.execute("SELECT status FROM plan WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise ValidationError("Planen finnes ikke.")
    if row[0] == "ferdig":
        raise ValidationError(FINISHED_PLAN)


def list_field_seasons(conn: sqlite3.Connection, plan_id: int) -> list[FieldSeason]:
    rows = conn.execute("SELECT * FROM field_season WHERE plan_id = ? ORDER BY id", (plan_id,))
    return [_from_row(FieldSeason, r) for r in rows]


def get_field_season(conn: sqlite3.Connection, season_id: int) -> FieldSeason | None:
    row = conn.execute("SELECT * FROM field_season WHERE id = ?", (season_id,)).fetchone()
    return _from_row(FieldSeason, row) if row else None


def add_field_seasons(conn: sqlite3.Connection, plan_id: int, seasons: list[FieldSeason]) -> None:
    with transaction(conn):
        _require_draft(conn, plan_id)
        for season in seasons:
            season.plan_id = plan_id
            _insert(conn, "field_season", season)


def save_field_season(conn: sqlite3.Connection, s: FieldSeason,
                      growth_classes: Iterable[str] | None = None) -> FieldSeason:
    """Update the advisor's inputs for a field season (draft plans only)."""
    s = clean_field_season(s)
    errors = field_season_errors(s, growth_classes)
    if errors:
        raise ValidationError(errors)
    with transaction(conn):
        _require_draft(conn, s.plan_id)
        if s.soil_sample_id is not None and not _count(
            conn,
            "SELECT COUNT(*) FROM soil_sample ss JOIN plan p ON p.enterprise_id = ss.enterprise_id "
            "WHERE ss.id = ? AND p.id = ?",
            (s.soil_sample_id, s.plan_id),
        ):
            raise ValidationError("Jordprøven hører ikke til dette foretaket.")
        _update(conn, "field_season", s)
    return get_field_season(conn, s.id)


def set_requirement(conn: sqlite3.Connection, season_id: int, n: float | None, p: float | None,
                    k: float | None, rationale: str | None) -> None:
    """Store computed requirement values (draft plans only)."""
    with transaction(conn):
        row = conn.execute("SELECT plan_id FROM field_season WHERE id = ?", (season_id,)).fetchone()
        _require_draft(conn, row[0])
        conn.execute(
            "UPDATE field_season SET req_n = ?, req_p = ?, req_k = ?, rationale = ?, updated_at = ? WHERE id = ?",
            (n, p, k, rationale, now_iso(), season_id),
        )


def list_applications(conn: sqlite3.Connection, plan_id: int, kind: str | None = None) -> list[Application]:
    sql = ("SELECT a.* FROM application a JOIN field_season fs ON fs.id = a.field_season_id "
           "WHERE fs.plan_id = ?")
    params: list = [plan_id]
    if kind is not None:
        sql += " AND a.kind = ?"
        params.append(kind)
    rows = conn.execute(sql + " ORDER BY coalesce(a.date, ''), a.id", params)
    return [_from_row(Application, r) for r in rows]


def get_application(conn: sqlite3.Connection, app_id: int) -> Application | None:
    row = conn.execute("SELECT * FROM application WHERE id = ?", (app_id,)).fetchone()
    return _from_row(Application, row) if row else None


def _season_plan_id(conn: sqlite3.Connection, season_id: int) -> int:
    row = conn.execute("SELECT plan_id FROM field_season WHERE id = ?", (season_id,)).fetchone()
    if row is None:
        raise ValidationError("Skiftet finnes ikke i planen.")
    return row[0]


def save_application(conn: sqlite3.Connection, a: Application, product_codes: Iterable[str] | None = None,
                     timing_codes: Iterable[str] | None = None) -> Application:
    """Planned applications can only change on draft plans; actual ones (journal) always."""
    a = clean_application(a)
    errors = application_errors(a, product_codes, timing_codes)
    if errors:
        raise ValidationError(errors)
    with transaction(conn):
        plan_id = _season_plan_id(conn, a.field_season_id)
        if a.kind == "planlagt":
            _require_draft(conn, plan_id)
        if a.id is None:
            a.id = _insert(conn, "application", a)
        else:
            old = get_application(conn, a.id)
            if old is not None and old.kind == "planlagt":
                _require_draft(conn, _season_plan_id(conn, old.field_season_id))
            _update(conn, "application", a)
    return get_application(conn, a.id)


def delete_application(conn: sqlite3.Connection, app_id: int) -> None:
    with transaction(conn):
        a = get_application(conn, app_id)
        if a is None:
            return
        if a.kind == "planlagt":
            _require_draft(conn, _season_plan_id(conn, a.field_season_id))
        conn.execute("DELETE FROM application WHERE id = ?", (app_id,))


def replace_planned_applications(conn: sqlite3.Connection, season_id: int, apps: list[Application],
                                 product_codes: Iterable[str] | None = None,
                                 timing_codes: Iterable[str] | None = None) -> None:
    """Swap all planned applications of a field season (used by templates)."""
    cleaned = [clean_application(replace(a, field_season_id=season_id, kind="planlagt")) for a in apps]
    errors = [e for a in cleaned for e in application_errors(a, product_codes, timing_codes)]
    if errors:
        raise ValidationError(errors)
    with transaction(conn):
        _require_draft(conn, _season_plan_id(conn, season_id))
        conn.execute("DELETE FROM application WHERE field_season_id = ? AND kind = 'planlagt'", (season_id,))
        for a in cleaned:
            _insert(conn, "application", a)


def finalize_plan(conn: sqlite3.Connection, plan_id: int, snapshot_json: str, finalized_at: str) -> Plan:
    with transaction(conn):
        _require_draft(conn, plan_id)
        conn.execute(
            "UPDATE plan SET status = 'ferdig', finalized_at = ?, snapshot_json = ?, updated_at = ? WHERE id = ?",
            (finalized_at, snapshot_json, now_iso(), plan_id),
        )
    return get_plan(conn, plan_id)


def move_actual_applications(conn: sqlite3.Connection, from_plan_id: int, to_plan_id: int) -> None:
    """Move journal entries to the field seasons of the same fields in another plan."""
    with transaction(conn):
        target = {r[0]: r[1] for r in conn.execute(
            "SELECT field_id, id FROM field_season WHERE plan_id = ?", (to_plan_id,))}
        rows = conn.execute(
            "SELECT a.id, fs.field_id FROM application a JOIN field_season fs ON fs.id = a.field_season_id "
            "WHERE fs.plan_id = ? AND a.kind = 'utfort'", (from_plan_id,),
        ).fetchall()
        for app_id, field_id in rows:
            if field_id in target:
                conn.execute("UPDATE application SET field_season_id = ?, updated_at = ? WHERE id = ?",
                             (target[field_id], now_iso(), app_id))


# --- Settings ---------------------------------------------------------------------------


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM setting")}


def load_settings(conn: sqlite3.Connection) -> Settings:
    """Typed calculation settings. Raises ValidationError if a value is missing or invalid."""
    raw = get_settings(conn)
    errors: list[str] = []
    values: dict[str, float] = {}
    for key in ("p_skew_factor", "plan_threshold_daa", "soil_max_age_years"):
        try:
            value = parse_number(raw.get(key))
        except ValueError:
            value = None
        if value is None or value < 0:
            errors.append(f"Innstillingen «{key}» mangler eller er ugyldig.")
        values[key] = value
    if values["soil_max_age_years"] is not None and not values["soil_max_age_years"].is_integer():
        errors.append("Innstillingen «soil_max_age_years» må være et heltall.")
    if raw.get("apply_mold_correction") not in ("0", "1"):
        errors.append("Innstillingen «apply_mold_correction» må være 0 eller 1.")
    if errors:
        raise ValidationError(errors)
    return Settings(
        apply_mold_correction=raw["apply_mold_correction"] == "1",
        p_skew_factor=values["p_skew_factor"],
        plan_threshold_daa=values["plan_threshold_daa"],
        soil_max_age_years=int(values["soil_max_age_years"]),
    )


def save_settings(conn: sqlite3.Connection, values: dict[str, str | None]) -> None:
    with transaction(conn):
        conn.executemany(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(values.items()),
        )


# --- Raw data -------------------------------------------------------------------------------


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in rows]


def read_table(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    if table not in list_tables(conn):  # table names can't be bound as parameters
        raise ValueError(f"Ukjent tabell: {table}")
    cur = conn.execute(f'SELECT * FROM "{table}"')
    columns = [d[0] for d in cur.description]
    return columns, [tuple(r) for r in cur.fetchall()]


def product_in_use(conn: sqlite3.Connection, product_code: str) -> int:
    """Number of planned/actual applications that use a product code."""
    return _count(conn, "SELECT COUNT(*) FROM application WHERE product_code = ?", (product_code,))


def list_draft_plans(conn: sqlite3.Connection) -> list[Plan]:
    return [_from_row(Plan, r) for r in conn.execute(
        "SELECT * FROM plan WHERE status = 'utkast' ORDER BY enterprise_id, year, version")]
