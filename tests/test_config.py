import shutil
import sys

import pytest

from gjodsel import config


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """Pretend to be the PyInstaller build: bundle with reference CSVs and demo data."""
    bundle = tmp_path / "bundle"
    shutil.copytree(config.REFERENCE_DIR, bundle / "reference")
    (bundle / "demo" / "attachments" / "1").mkdir(parents=True)
    (bundle / "demo" / "gjodselplan.db").write_bytes(b"demo-db")
    (bundle / "demo" / "attachments" / "1" / "demokart.png").write_bytes(b"png")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.delenv("GJODSEL_DATA", raising=False)
    return bundle


def test_dev_paths(monkeypatch):
    monkeypatch.delenv("GJODSEL_DATA", raising=False)
    paths = config.get_paths()
    assert paths.data_dir == config.PROJECT_DIR / "data"
    assert paths.reference_dir == config.REFERENCE_DIR


def test_frozen_windows_uses_appdata(frozen, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    paths = config.get_paths()
    assert paths.data_dir == tmp_path / "Roaming" / "Gjodselplan"
    assert paths.reference_dir == paths.data_dir / "reference"
    assert paths.feedback_file == paths.data_dir / "feedback.csv"
    assert config.bundled_reference_dir() == frozen / "reference"


def test_first_start_copies_reference_and_demo(frozen, tmp_path, monkeypatch):
    monkeypatch.setenv("GJODSEL_DATA", str(tmp_path / "data"))
    paths = config.get_paths()
    notes = config.prepare_data_dir(paths)
    assert "demo" in notes
    assert len(list(paths.reference_dir.glob("*.csv"))) == 11
    assert paths.db_file.read_bytes() == b"demo-db"
    assert (paths.attachments_dir / "1" / "demokart.png").exists()

    # Second start: a reviewer's edit and their database are kept; only missing files come back.
    (paths.reference_dir / "cultures.csv").write_text("redigert", encoding="utf-8")
    (paths.reference_dir / "timings.csv").unlink()
    paths.db_file.write_bytes(b"egne data")
    notes = config.prepare_data_dir(paths)
    assert "demo" not in notes
    assert (paths.reference_dir / "cultures.csv").read_text(encoding="utf-8") == "redigert"
    assert (paths.reference_dir / "timings.csv").exists()
    assert paths.db_file.read_bytes() == b"egne data"


def test_dev_mode_never_installs_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("GJODSEL_DATA", str(tmp_path / "data"))
    paths = config.get_paths()
    assert config.prepare_data_dir(paths) == []
    assert not paths.db_file.exists()
