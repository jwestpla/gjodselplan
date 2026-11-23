"""Generated farm maps: which points go on a property's map, and the map data used by the plan (DB + farm_map)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

from . import farm_map, repo
from .farm_map import FarmMap, MapPoint
from .models import Property
from .units import format_date


def property_points(conn: sqlite3.Connection, enterprise_id: int, property_id: int
                    ) -> tuple[list[MapPoint], list[str]]:
    """Soil samples linked to the property's active fields, and those fields, as map points.

    Also returns the names of linked samples without coordinates (§ 29 asks for them).
    """
    fields = [f for f in repo.list_fields(conn, enterprise_id, include_inactive=False) if f.property_id == property_id]
    field_ids = {f.id for f in fields}
    links = repo.sample_field_links(conn, enterprise_id)
    points: list[MapPoint] = []
    missing: list[str] = []
    for s in repo.list_soil_samples(conn, enterprise_id):
        if not field_ids & set(links.get(s.id, [])):
            continue
        name = sample_name(s.label, s.sample_date)
        if s.lat is None or s.lon is None:
            missing.append(name)
        else:
            points.append(MapPoint("sample", name, s.lat, s.lon))
    points += [MapPoint("field", f"Skifte {f.field_no}", f.lat, f.lon)
               for f in fields if f.lat is not None and f.lon is not None]
    return points, missing


def sample_name(label: str | None, sample_date: str | None) -> str:
    return (label or "").strip() or f"Prøve {format_date(sample_date)}"


def view_data(conn: sqlite3.Connection, enterprise_id: int, property_id: int, fm: FarmMap) -> dict:
    """JSON-serialisable map data for the PlanView (and its snapshot)."""
    points, missing = property_points(conn, enterprise_id, property_id)
    return {**fm.to_dict(), "points": [asdict(p) for p in points], "missing_coordinates": missing}


def from_view_data(data: dict) -> tuple[FarmMap, list[MapPoint], list[str]]:
    return (FarmMap.from_dict(data), [MapPoint(**p) for p in data.get("points", [])],
            list(data.get("missing_coordinates", [])))


def fetch(data_dir: Path, prop: Property, points: list[MapPoint], background_kind: str,
          get: farm_map.Getter | None = None) -> FarmMap:
    """Fetch and cache the map of one property. No database access: safe to run in a worker thread."""
    fm, image = farm_map.fetch_map(prop.kommunenr, prop.gnr, prop.bnr, prop.fnr, prop.matrikkel, points,
                                   background_kind, get)
    return farm_map.save_cache(data_dir, prop.enterprise_id, prop.id, fm, image)


def frozen_path(enterprise_id: int, plan_id: int, property_id: int) -> str:
    return f"attachments/{enterprise_id}/planer/plan_{plan_id}_kart_eiendom_{property_id}.jpg"
