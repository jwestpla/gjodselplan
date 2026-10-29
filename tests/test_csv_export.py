import csv
from datetime import date

from gjodsel import repo
from gjodsel.csv_export import export_all


def test_export_all_tables_excel_friendly(conn, farm, tmp_path):
    folder = export_all(conn, tmp_path / "export", today=date(2026, 10, 27))
    assert folder.name == "2026-10-27"
    assert {p.stem for p in folder.glob("*.csv")} == set(repo.list_tables(conn))

    raw = (folder / "property.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM so Excel detects UTF-8
    assert "Øvre Testgard".encode("utf-8") in raw

    with open(folder / "field.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    assert rows[0]["name"] == "Eplehagen"
    assert rows[0]["area_daa"] == "10,8"       # decimal comma
    assert rows[0]["variety"] == ""            # NULL -> empty


def test_export_quotes_text_with_separator(conn, farm, tmp_path):
    e, _, _ = farm
    e.notes = "Merk; to linjer\nandre linje"
    repo.save_enterprise(conn, e)
    folder = export_all(conn, tmp_path)
    with open(folder / "enterprise.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    assert rows[0]["notes"] == "Merk; to linjer\nandre linje"
