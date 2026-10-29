from gjodsel import db, plan_service, repo
from gjodsel.config import REFERENCE_DIR
from gjodsel.demo import DEMO_NAME, build_demo, enterprise_names
from gjodsel.reference import load_reference


def test_demo_database(tmp_path):
    db_file = build_demo(tmp_path, REFERENCE_DIR, 2026)
    assert enterprise_names(db_file) == [DEMO_NAME]
    conn = db.connect(db_file)
    e = repo.list_enterprises(conn)[0]
    assert not e.org_nr and not e.phone and not e.email
    assert [p.kommunenr for p in repo.list_properties(conn, e.id)] == ["9999"]  # no real property
    plan = repo.list_plans(conn, e.id, 2026)[0]
    assert plan.status == "utkast"
    view = plan_service.build_view(conn, load_reference(REFERENCE_DIR), repo.load_settings(conn), plan)
    stages = {r.field_no: r.stage for r in view.rows}
    assert stages == {"1": "bærende", "2": "planteår", "3": "bærende", "4": "bærende"}
    assert {r.culture_code for r in view.rows} == {"eple", "plomme", "sotkirsebaer"}
    assert view.soil_missing == ["4 Morell"]                                        # expired sample
    assert any(s.values["p_al"] == 24.9 for s in view.samples)                      # high P-AL (57 P2O5)
    assert all(r.planned for r in view.rows)
    assert (tmp_path / "attachments" / str(e.id) / "demokart.png").exists()
    conn.close()
