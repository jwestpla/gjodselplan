import sqlite3

import pytest

from gjodsel import db


def _enterprise(conn, name="Testgård"):
    return conn.execute("INSERT INTO enterprise (name) VALUES (?)", (name,)).lastrowid


def _field(conn, enterprise_id):
    prop = conn.execute(
        "INSERT INTO property (enterprise_id, kommunenr, gnr, bnr) VALUES (?, '4640', 1, 2)",
        (enterprise_id,),
    ).lastrowid
    return conn.execute(
        "INSERT INTO field (property_id, field_no, name, area_daa, culture_code) "
        "VALUES (?, '1', 'Testskifte', 10.8, 'eple')",
        (prop,),
    ).lastrowid


def test_migrate_creates_schema_and_is_idempotent(conn):
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    assert db.migrate(conn) == len(db.MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"enterprise", "property", "field", "soil_sample", "field_soil_sample", "plan",
            "field_season", "application", "setting", "schema_version"} <= tables


def test_default_settings_seeded(conn):
    settings = dict(conn.execute("SELECT key, value FROM setting").fetchall())
    assert settings["apply_mold_correction"] == "0"
    assert settings["p_skew_factor"] == "1.10"
    assert settings["plan_threshold_daa"] == "25"
    assert settings["soil_max_age_years"] == "8"


def test_newer_schema_refused(conn):
    conn.execute("INSERT INTO schema_version VALUES (?, 'x')", (len(db.MIGRATIONS) + 1,))
    with pytest.raises(RuntimeError, match="nyere versjon"):
        db.migrate(conn)


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + ["CREATE TABLE t (x); SELECT * FROM nope;"])
    c = db.connect(tmp_path / "test.db")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(c)
    assert db.schema_version(c) == len(db.MIGRATIONS) - 1
    assert c.execute("SELECT name FROM sqlite_master WHERE name = 't'").fetchone() is None
    assert not c.in_transaction


def test_timestamps_default(conn):
    eid = _enterprise(conn)
    row = conn.execute("SELECT created_at, updated_at FROM enterprise WHERE id = ?", (eid,)).fetchone()
    assert row["created_at"] and row["created_at"] == row["updated_at"]
    assert "T" in row["created_at"]


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO field (property_id, field_no, name, area_daa, culture_code) "
            "VALUES (999, '1', 'x', 1, 'eple')"
        )


def test_field_with_plan_cannot_be_deleted(conn):
    eid = _enterprise(conn)
    fid = _field(conn, eid)
    plan = conn.execute("INSERT INTO plan (enterprise_id, year) VALUES (?, 2026)", (eid,)).lastrowid
    conn.execute("INSERT INTO field_season (plan_id, field_id) VALUES (?, ?)", (plan, fid))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM field WHERE id = ?", (fid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM enterprise WHERE id = ?", (eid,))


@pytest.mark.parametrize("sql", [
    "INSERT INTO plan (enterprise_id, year, version) VALUES (:e, 2026, 2)",
    "INSERT INTO plan (enterprise_id, year, status) VALUES (:e, 2026, 'godkjent')",
    "INSERT INTO field_season (plan_id, field_id, override_n) VALUES (:p, :f, 5)",
    "INSERT INTO application (field_season_id, kind, product_code, kg_per_daa) "
    "VALUES (:s, 'utfort', 'kalksalpeter', 10)",
    "INSERT INTO application (field_season_id, kind, product_code, kg_per_daa) "
    "VALUES (:s, 'planlagt', 'kalksalpeter', -1)",
    "INSERT INTO soil_sample (enterprise_id, sample_date, method) VALUES (:e, '2025-04-01', 'Mehlich')",
])
def test_check_constraints(conn, sql):
    eid = _enterprise(conn)
    fid = _field(conn, eid)
    pid = conn.execute("INSERT INTO plan (enterprise_id, year) VALUES (?, 2025)", (eid,)).lastrowid
    # Season on another plan, so the override case can't trip UNIQUE(plan_id, field_id) instead.
    other = conn.execute("INSERT INTO plan (enterprise_id, year) VALUES (?, 2024)", (eid,)).lastrowid
    sid = conn.execute(
        "INSERT INTO field_season (plan_id, field_id) VALUES (?, ?)", (other, fid)
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, {"e": eid, "f": fid, "p": pid, "s": sid})


def test_transaction_rolls_back(conn):
    with pytest.raises(ValueError):
        with db.transaction(conn):
            _enterprise(conn, "Skal bort")
            raise ValueError
    assert conn.execute("SELECT COUNT(*) FROM enterprise").fetchone()[0] == 0


def test_backup_and_prune(tmp_path):
    db_file = tmp_path / "gjodselplan.db"
    backups = tmp_path / "backups"
    assert db.backup_database(db_file, backups, keep=3) is None  # no DB yet

    c = db.connect(db_file)
    db.migrate(c)
    _enterprise(c)
    c.close()

    for _ in range(5):
        db.backup_database(db_file, backups, keep=3)
    copies = sorted(backups.glob("gjodselplan_*.db"))
    assert len(copies) == 3

    copy = sqlite3.connect(copies[-1])
    assert copy.execute("SELECT name FROM enterprise").fetchone()[0] == "Testgård"
    copy.close()


def test_open_database_backs_up_before_migrating(tmp_path):
    db_file = tmp_path / "gjodselplan.db"
    backups = tmp_path / "backups"
    db.open_database(db_file, backups, keep=20).close()
    assert not list(backups.glob("*.db"))  # first start: nothing to back up
    db.open_database(db_file, backups, keep=20).close()
    assert len(list(backups.glob("*.db"))) == 1
