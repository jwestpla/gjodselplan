import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from gjodsel.config import REFERENCE_DIR
from gjodsel.reference import ReferenceDataError, load_reference
from gjodsel.reference_review import (
    DELETED_ROW,
    NEW_ROW,
    TABLE_INFO,
    EditSession,
    check_cell,
    load_review_status,
    make_review_package,
    read_changes,
    read_raw,
    review_counts,
    row_key,
    save_session,
    set_review,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import merge_review  # noqa: E402


@pytest.fixture
def ref_dir(tmp_path):
    target = tmp_path / "reference"
    shutil.copytree(REFERENCE_DIR, target)
    return target


@pytest.fixture
def files(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    return data / "reference_changes.csv", data / "review_status.csv"


def _row_id(session, key):
    return next(r.id for r in session.rows if row_key(session.file, r.values) == key)


def test_every_reference_file_has_info():
    assert {i.file for i in TABLE_INFO} == {p.name for p in REFERENCE_DIR.glob("*.csv")}


def test_row_keys(ref_dir):
    rows = read_raw(ref_dir / "k_correction.csv").rows
    assert row_key("k_correction.csv", rows[0]) == "K-HNO₃ 0–30 / K-AL 0–6,5"
    assert row_key("p_al_correction.csv", {"pal_min": "4.5", "pal_max": "9,5"}) == "P-AL 4,5–9,5"
    assert row_key("templates.csv", {"template": "Nyplanting", "order": "2"}) == "Nyplanting #2"


def test_check_cell():
    assert check_cell("cultures.csv", "n_base", "4,5") is None
    assert "ikke et tall" in check_cell("cultures.csv", "n_base", "fire")
    assert "tom" in check_cell("cultures.csv", "n_base", "")
    assert "heltall" in check_cell("age_factors.csv", "years_min", "1,5")
    assert "0 eller 1" in check_cell("age_factors.csv", "apply_yield_adj", "2")
    assert check_cell("products.csv", "mg", "") is None  # optional


def test_save_change_logs_and_marks_endret(ref_dir, files):
    changes_path, status_path = files
    session = EditSession.load(ref_dir, "cultures.csv")
    rid = _row_id(session, "paere")
    session.set(rid, "n_base", "6,0")
    session.set(rid, "source", "NIBIO GH tab.7 – verifisert")
    changes = save_session(ref_dir, session, "Kari", changes_path, status_path)
    assert {(c.row_key, c.column, c.old, c.new) for c in changes} == {
        ("paere", "n_base", "6,5", "6,0"),
        ("paere", "source", "NIBIO GH tab.7; yield_basis/n_per_tonne ANTATT", "NIBIO GH tab.7 – verifisert"),
    }
    assert load_reference(ref_dir).cultures["paere"].n_base == 6.0
    log = read_changes(changes_path)
    assert len(log) == 2 and log[0]["reviewer"] == "Kari"
    assert load_review_status(status_path)[("cultures.csv", "paere")].status == "Endret"
    raw = (ref_dir / "cultures.csv").read_text(encoding="utf-8-sig")
    assert '"NIBIO GH tab.7; yield_basis/n_per_tonne ANTATT"' in raw  # untouched rows keep their quoting


def test_save_refuses_gap(ref_dir, files):
    changes_path, status_path = files
    before = (ref_dir / "p_al_correction.csv").read_bytes()
    session = EditSession.load(ref_dir, "p_al_correction.csv")
    session.set(_row_id(session, "P-AL 2,5–3,5"), "pal_min", "2,6")
    with pytest.raises(ReferenceDataError) as exc:
        save_session(ref_dir, session, "Kari", changes_path, status_path)
    assert any("hull" in e for e in exc.value.errors)
    assert (ref_dir / "p_al_correction.csv").read_bytes() == before
    assert not changes_path.exists()


def test_band_edit_renames_review_key(ref_dir, files):
    changes_path, status_path = files
    set_review(status_path, "p_al_correction.csv", "P-AL 4,5–9,5", "Uenig", "Bør være 4,5–10", "Kari")
    session = EditSession.load(ref_dir, "p_al_correction.csv")
    session.set(_row_id(session, "P-AL 4,5–9,5"), "pal_max", "10")
    session.set(_row_id(session, "P-AL 9,5–13,5"), "pal_min", "10")
    save_session(ref_dir, session, "Kari", changes_path, status_path)
    entries = load_review_status(status_path)
    assert ("p_al_correction.csv", "P-AL 4,5–9,5") not in entries
    moved = entries[("p_al_correction.csv", "P-AL 4,5–10")]
    assert moved.status == "Endret" and moved.comment == "Bør være 4,5–10"


def test_add_and_delete_rows(ref_dir, files):
    changes_path, status_path = files
    session = EditSession.load(ref_dir, "products.csv")
    new = session.add_row(copy_of=_row_id(session, "kalksalpeter"))
    session.set(new.id, "code", "kalksalpeter_b")
    session.delete_row(_row_id(session, "multi_k_mg"))
    changes = save_session(ref_dir, session, "Kari", changes_path, status_path)
    kinds = {(c.row_key, c.column) for c in changes}
    assert ("kalksalpeter_b", NEW_ROW) in kinds and ("multi_k_mg", DELETED_ROW) in kinds
    assert "kalksalpeter_b" in load_reference(ref_dir).products


def test_review_status_and_counts(ref_dir, files):
    _, status_path = files
    counts = review_counts(ref_dir, status_path)
    assert counts["cultures.csv"] == (0, 4)          # paere, sotkirsebaer, surkirsebaer, plomme
    assert counts["p_al_correction.csv"] == (0, 0)   # all NIBIO-sourced
    set_review(status_path, "cultures.csv", "plomme", "Godkjent", "", "Kari")
    set_review(status_path, "p_al_correction.csv", "P-AL 0–1,5", "Uenig", "Sjekk", "Kari")
    counts = review_counts(ref_dir, status_path)
    assert counts["cultures.csv"] == (1, 4)
    assert counts["p_al_correction.csv"] == (1, 1)   # reviewed although not ANTATT
    set_review(status_path, "cultures.csv", "plomme", "Ikke vurdert", "", "Kari")
    assert ("cultures.csv", "plomme") not in load_review_status(status_path)


def test_package_and_merge(ref_dir, files, tmp_path):
    changes_path, status_path = files
    data_dir = changes_path.parent
    bundled = tmp_path / "bundled"
    shutil.copytree(REFERENCE_DIR, bundled)
    session = EditSession.load(ref_dir, "cultures.csv")
    session.set(_row_id(session, "paere"), "n_base", "6,0")
    save_session(ref_dir, session, "Kari Ås", changes_path, status_path)
    set_review(status_path, "products.csv", "kalksalpeter", "Uenig", "N er 15,5 % ifølge etikett", "Kari Ås")
    (data_dir / "feedback.csv").write_text("tidspunkt;kommentar\nx;fint\n", encoding="utf-8")

    package = make_review_package(ref_dir, bundled, data_dir, "Kari Ås", "0.1.0", tmp_path / "Documents")
    assert package.name.startswith("Gjennomgang_Kari_Ås_") and package.suffix == ".zip"
    with zipfile.ZipFile(package) as z:
        assert set(z.namelist()) == {"reference/cultures.csv", "review_status.csv", "reference_changes.csv",
                                     "feedback.csv", "manifest.txt"}
    again = make_review_package(ref_dir, bundled, data_dir, "Kari Ås", "0.1.0", tmp_path / "Documents")
    assert again != package  # never overwrites

    repo_ref = tmp_path / "repo_reference"
    shutil.copytree(REFERENCE_DIR, repo_ref)
    assert merge_review.main([str(package), "--reference-dir", str(repo_ref)]) == 0  # show only
    assert load_reference(repo_ref).cultures["paere"].n_base == 6.5
    assert merge_review.main([str(package), "--reference-dir", str(repo_ref), "--apply", "--yes"]) == 0
    assert load_reference(repo_ref).cultures["paere"].n_base == 6.0


def test_merge_one_by_one_and_invalid(tmp_path, ref_dir, files):
    changes_path, status_path = files
    session = EditSession.load(ref_dir, "cultures.csv")
    session.set(_row_id(session, "paere"), "n_base", "6,0")
    session.set(_row_id(session, "plomme"), "k_base", "7,5")
    save_session(ref_dir, session, "Kari", changes_path, status_path)
    package = make_review_package(ref_dir, REFERENCE_DIR, changes_path.parent, "Kari", "0.1.0", tmp_path)
    repo_ref = tmp_path / "repo_reference"
    shutil.copytree(REFERENCE_DIR, repo_ref)
    answers = iter(["e", "j", "n"])  # one by one: accept first change, reject second
    assert merge_review.main([str(package), "--reference-dir", str(repo_ref), "--apply"], ask=lambda _: next(answers)) == 0
    merged = load_reference(repo_ref).cultures
    assert merged["paere"].n_base == 6.0 and merged["plomme"].k_base == 8.0

    # A package whose file breaks validation is refused and nothing is written.
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("reference/p_al_correction.csv",
                   (REFERENCE_DIR / "p_al_correction.csv").read_text(encoding="utf-8-sig").replace("2,5;3,5;50", "2,6;3,5;50"))
    before = (repo_ref / "p_al_correction.csv").read_bytes()
    assert merge_review.main([str(bad), "--reference-dir", str(repo_ref), "--apply", "--yes"]) == 1
    assert (repo_ref / "p_al_correction.csv").read_bytes() == before
