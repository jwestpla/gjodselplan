"""Generated farm maps: which points go on a property's map, and the map data used by the plan (DB + farm_map)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from datetime import datetime

from . import farm_map, gardskart, repo
from .farm_map import FarmMap, MapPoint, Teig
from .gardskart import GardskartError, GardskartMismatch, Georef, GardskartPdf
from .geo import wgs84_to_utm
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


# --- Aerial photo from the uploaded gårdskart ------------------------------------------------------------


@dataclass
class PhotoAttempt:
    """A georeferenced (or best-guess) aerial photo, not saved yet."""
    pdf: GardskartPdf
    georef: Georef
    teiger_lonlat: list


def uploaded_gardskart(data_dir: Path, prop: Property) -> GardskartPdf:
    """The property's uploaded gårdskart, checked against the property. Raises GardskartError."""
    if not prop.map_attachment or not prop.map_attachment.lower().endswith(".pdf"):
        raise GardskartError("Last opp gårdskartet som PDF først (Legg ved gårdskart…).")
    path = data_dir / prop.map_attachment
    if not path.is_file():
        raise GardskartError(f"Finner ikke gårdskartet {prop.map_attachment}.")
    pdf = gardskart.read_gardskart(path)
    wanted = gardskart.matrikkel_text(prop.kommunenr, prop.gnr, prop.bnr, prop.fnr)
    if pdf.matrikkel and pdf.matrikkel != wanted:
        raise GardskartMismatch(f"Gårdskartet gjelder {pdf.matrikkel}, men eiendommen er {wanted}. "
                             "Last opp gårdskartet for riktig eiendom.")
    return pdf


def prepare_photo_map(data_dir: Path, prop: Property, get: farm_map.Getter | None = None) -> PhotoAttempt:
    """Read the gårdskart, fetch Kartverket's boundary and place the photo. Network, no database: thread-safe."""
    pdf = uploaded_gardskart(data_dir, prop)
    get = get or farm_map.http_get
    try:
        raw = farm_map.parse_boundary(json.loads(get(farm_map.boundary_url(prop.kommunenr, prop.gnr, prop.bnr,
                                                                           prop.fnr))[1]))
    except ValueError as exc:
        raise farm_map.MapUnavailable("Kartverket svarte med noe som ikke kunne leses. Prøv igjen senere.") from exc
    return PhotoAttempt(pdf, gardskart.georeference(pdf, raw), raw)


def photo_farm_map(prop: Property, attempt: PhotoAttempt, georef: Georef | None = None) -> FarmMap:
    georef = georef or attempt.georef
    placed = gardskart.photo_map(attempt.pdf, georef)
    teiger = [Teig([[list(wgs84_to_utm(lat, lon, georef.zone)) for lon, lat, *_ in ring] for ring in rings], main, acc)
              for rings, main, acc in attempt.teiger_lonlat]
    printed = attempt.pdf.printed or "ukjent dato"
    notes = ["Flyfotoet er plassert manuelt."] if georef.method == "manuell" else []
    return FarmMap(
        zone=georef.zone, extent=placed.extent, width=placed.photo_size[0], height=placed.photo_size[1],
        background_kind=farm_map.PHOTO, background="", matrikkel=prop.matrikkel,
        fetched_at=datetime.now().isoformat(timespec="seconds"), teiger=teiger, notes=notes,
        attribution=f"Flyfoto: Norge i bilder (Kartverket, NIBIO, Statens vegvesen), fra NIBIO Gårdskart utskrevet "
                    f"{printed}; fotodato står ikke i gårdskartet. Eiendomsgrenser: © Kartverket (CC BY 4.0).",
        georef={**georef.to_dict(), **placed.source},
    )


def save_photo_map(data_dir: Path, prop: Property, attempt: PhotoAttempt, georef: Georef | None = None) -> FarmMap:
    fm = photo_farm_map(prop, attempt, georef)
    return farm_map.save_cache(data_dir, prop.enterprise_id, prop.id, fm, attempt.pdf.photo_jpeg, farm_map.PHOTO)


def save_photo_alignment(data_dir: Path, prop: Property, fm: FarmMap, d_e: float, d_n: float) -> FarmMap:
    """Move an already saved aerial photo map by hand (metres); the stored photo is kept."""
    moved = fm.shifted(d_e, d_n)
    georef = Georef.from_dict(moved.georef).shifted(d_e, d_n)
    moved.georef = {**moved.georef, **georef.to_dict()}
    moved.notes = ["Flyfotoet er plassert manuelt."]
    return farm_map.save_cache(data_dir, prop.enterprise_id, prop.id, moved, None, farm_map.PHOTO)


def photo_is_current(data_dir: Path, prop: Property, fm: FarmMap) -> bool:
    """False when the uploaded gårdskart was replaced after the photo was placed."""
    import hashlib

    if not prop.map_attachment or not (data_dir / prop.map_attachment).is_file():
        return False
    return hashlib.sha256((data_dir / prop.map_attachment).read_bytes()).hexdigest() == fm.georef.get("sha256")
