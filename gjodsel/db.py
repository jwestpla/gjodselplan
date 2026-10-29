"""SQLite connection, schema migrations and startup backup."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

BACKUP_PREFIX = "gjodselplan_"

_NOW = "(strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))"
_STAMPS = f"created_at TEXT NOT NULL DEFAULT {_NOW},\n  updated_at TEXT NOT NULL DEFAULT {_NOW}"

_SCHEMA_V1 = f"""
CREATE TABLE enterprise (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,                        -- foretaksnavn
  org_nr TEXT UNIQUE,                        -- 9 digits, mod11; NULL when unknown
  contact_first_name TEXT,
  contact_last_name TEXT,
  street TEXT,
  postcode TEXT,
  place TEXT,
  phone TEXT,
  email TEXT,
  region TEXT NOT NULL DEFAULT 'standard',   -- key into p_limits.csv
  notes TEXT,
  {_STAMPS}
);

CREATE TABLE property (                      -- landbrukseiendom (owned or leased)
  id INTEGER PRIMARY KEY,
  enterprise_id INTEGER NOT NULL REFERENCES enterprise(id) ON DELETE RESTRICT,
  farm_name TEXT,
  kommunenr TEXT NOT NULL,
  gnr INTEGER NOT NULL,
  bnr INTEGER NOT NULL,
  fnr INTEGER NOT NULL DEFAULT 0,
  snr INTEGER NOT NULL DEFAULT 0,
  tenure TEXT NOT NULL DEFAULT 'eid' CHECK (tenure IN ('eid', 'leid')),
  map_attachment TEXT,                       -- relative path (kartskisse)
  notes TEXT,
  {_STAMPS}
);
CREATE INDEX idx_property_enterprise ON property(enterprise_id);

CREATE TABLE field (                         -- skifte
  id INTEGER PRIMARY KEY,
  property_id INTEGER NOT NULL REFERENCES property(id) ON DELETE RESTRICT,
  field_no TEXT NOT NULL,                    -- unique per enterprise (enforced in repo.py)
  name TEXT NOT NULL,
  area_daa REAL NOT NULL CHECK (area_daa > 0),
  culture_code TEXT NOT NULL,                -- cultures.csv
  variety TEXT,
  rootstock TEXT,
  planting_year INTEGER,
  row_spacing_m REAL,
  plant_spacing_m REAL,
  lat REAL,                                  -- WGS84 representative point
  lon REAL,
  polygon_geojson TEXT,
  irrigation TEXT DEFAULT 'ingen',           -- 'ingen' | 'drypp' | 'gjødselvanning'
  cover TEXT DEFAULT 'ingen',                -- 'ingen' | 'tunnel' | 'regntak'
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  notes TEXT,
  {_STAMPS}
);
CREATE INDEX idx_field_property ON field(property_id);

CREATE TABLE soil_sample (
  id INTEGER PRIMARY KEY,
  enterprise_id INTEGER NOT NULL REFERENCES enterprise(id) ON DELETE RESTRICT,
  label TEXT,                                -- name on lab report
  lab TEXT,
  lab_report_no TEXT,
  sample_date TEXT NOT NULL,                 -- ISO date
  depth_from_cm INTEGER,
  depth_to_cm INTEGER,
  lat REAL,                                  -- § 29: sample point coordinates
  lon REAL,
  ph REAL,
  p_al REAL,                                 -- ALWAYS mg P/100 g (converted on entry)
  k_al REAL,
  mg_al REAL,
  ca_al REAL,
  k_hno3 REAL,
  mold_pct REAL,
  soil_type TEXT,
  clay_class TEXT,
  method TEXT NOT NULL DEFAULT 'AL' CHECK (method IN ('AL', 'annet')),
  attachment TEXT,                           -- relative path under attachments/
  notes TEXT,
  {_STAMPS}
);
CREATE INDEX idx_soil_sample_enterprise ON soil_sample(enterprise_id);

CREATE TABLE field_soil_sample (
  field_id INTEGER NOT NULL REFERENCES field(id) ON DELETE CASCADE,
  soil_sample_id INTEGER NOT NULL REFERENCES soil_sample(id) ON DELETE CASCADE,
  {_STAMPS},
  PRIMARY KEY (field_id, soil_sample_id)
);
CREATE INDEX idx_field_soil_sample_sample ON field_soil_sample(soil_sample_id);

CREATE TABLE plan (
  id INTEGER PRIMARY KEY,
  enterprise_id INTEGER NOT NULL REFERENCES enterprise(id) ON DELETE RESTRICT,
  year INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  status TEXT NOT NULL DEFAULT 'utkast' CHECK (status IN ('utkast', 'ferdig')),
  change_reason TEXT,
  finalized_at TEXT,
  snapshot_json TEXT,                        -- frozen plan incl. reference rows used
  {_STAMPS},
  UNIQUE (enterprise_id, year, version),
  CHECK (version = 1 OR trim(coalesce(change_reason, '')) <> '')
);

CREATE TABLE field_season (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
  field_id INTEGER NOT NULL REFERENCES field(id) ON DELETE RESTRICT,
  fertilized INTEGER NOT NULL DEFAULT 1 CHECK (fertilized IN (0, 1)),
  expected_yield_kg_daa REAL,
  growth_class TEXT,                         -- n_growth_adjustment.csv
  forgrode TEXT,
  soil_sample_id INTEGER REFERENCES soil_sample(id),
  req_n REAL,
  req_p REAL,
  req_k REAL,
  override_n REAL,
  override_p REAL,
  override_k REAL,
  override_reason TEXT,
  rationale TEXT,
  notes TEXT,
  {_STAMPS},
  UNIQUE (plan_id, field_id),
  CHECK ((override_n IS NULL AND override_p IS NULL AND override_k IS NULL)
         OR trim(coalesce(override_reason, '')) <> '')
);
CREATE INDEX idx_field_season_field ON field_season(field_id);

CREATE TABLE application (
  id INTEGER PRIMARY KEY,
  field_season_id INTEGER NOT NULL REFERENCES field_season(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('planlagt', 'utfort')),
  product_code TEXT NOT NULL,
  kg_per_daa REAL NOT NULL CHECK (kg_per_daa >= 0),
  timing_code TEXT,                          -- timings.csv (planned)
  date TEXT,
  performed_by TEXT,
  method TEXT,
  note TEXT,
  {_STAMPS},
  CHECK (kind = 'planlagt' OR date IS NOT NULL)
);
CREATE INDEX idx_application_field_season ON application(field_season_id);

CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO setting (key, value) VALUES
  ('apply_mold_correction', '0'),
  ('p_skew_factor', '1.10'),
  ('plan_threshold_daa', '25'),
  ('soil_max_age_years', '8');
"""

# Append only. Never edit a migration that has shipped; add a new one.
MIGRATIONS: list[str] = [_SCHEMA_V1]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_file: Path | str) -> sqlite3.Connection:
    # Autocommit mode: transactions are explicit via transaction() / migrations.
    conn = sqlite3.connect(db_file, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations, each in its own transaction. Returns the new version."""
    current = schema_version(conn)
    if current > len(MIGRATIONS):
        raise RuntimeError(
            f"Databasen har skjemaversjon {current}, men programmet kjenner bare "
            f"til versjon {len(MIGRATIONS)}. Bruk en nyere versjon av programmet."
        )
    for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        script = (
            f"BEGIN;\n{sql}\n"
            f"INSERT INTO schema_version (version, applied_at) VALUES ({version}, '{now_iso()}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def backup_database(db_file: Path, backups_dir: Path, keep: int) -> Path | None:
    """Copy the DB to backups_dir with a timestamped name and prune old copies.

    Uses SQLite's online backup API so the copy is consistent. Returns the
    backup path, or None if there is no database yet.
    """
    if not db_file.exists():
        return None
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backups_dir / f"{BACKUP_PREFIX}{stamp}.db"
    n = 1
    while target.exists():
        target = backups_dir / f"{BACKUP_PREFIX}{stamp}_{n}.db"
        n += 1
    src = sqlite3.connect(db_file)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    prune_backups(backups_dir, keep)
    return target


def prune_backups(backups_dir: Path, keep: int) -> None:
    backups = sorted(backups_dir.glob(f"{BACKUP_PREFIX}*.db"))
    for old in backups[: max(len(backups) - keep, 0)]:
        old.unlink()


def open_database(db_file: Path, backups_dir: Path, keep: int) -> sqlite3.Connection:
    """Startup sequence: back up the existing DB, connect, migrate."""
    backup_database(db_file, backups_dir, keep)
    conn = connect(db_file)
    try:
        migrate(conn)
    except Exception:
        conn.close()
        raise
    return conn
