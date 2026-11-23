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


def test_update_reference_files_replaces_only_unedited_shipped_copies(tmp_path, monkeypatch):
    import hashlib

    from gjodsel import config, reference_versions

    source, target = tmp_path / "bundled", tmp_path / "installed"
    source.mkdir()
    target.mkdir()
    old = "code;value;source\na;1,5;NIBIO\n".encode("utf-8-sig")
    (source / "a.csv").write_bytes("code;value;source\na;2,0;NIBIO\n".encode("utf-8-sig"))  # new version
    (source / "b.csv").write_bytes("code;value;source\nb;2,0;NIBIO\n".encode("utf-8-sig"))
    (source / "c.csv").write_bytes("code;value;source\nc;1;NIBIO\n".encode("utf-8-sig"))
    (target / "a.csv").write_bytes(old)  # an untouched copy of the old version
    (target / "b.csv").write_bytes("code;value;source\nb;9,9;Kari\n".encode("utf-8-sig"))  # edited by the advisor
    (target / "c.csv").write_bytes((source / "c.csv").read_bytes())  # already current
    sha = hashlib.sha256(old).hexdigest()
    monkeypatch.setattr(reference_versions, "SHIPPED", {"a.csv": (sha,), "b.csv": (sha,), "c.csv": (sha,)})

    assert config.update_reference_files(source, target) == ["a.csv"]
    assert (target / "a.csv").read_bytes() == (source / "a.csv").read_bytes()
    assert b"9,9" in (target / "b.csv").read_bytes()
    assert [p.read_bytes() for p in (target / "tidligere").glob("a_*.csv")] == [old]
    assert config.update_reference_files(source, target) == []  # nothing left to do


def test_shipped_reference_versions_are_recorded():
    """Every earlier version of a bundled reference CSV must be in reference_versions.SHIPPED,
    otherwise installed copies of it would never be updated."""
    import hashlib
    import shutil
    import subprocess

    from gjodsel.config import REFERENCE_DIR
    from gjodsel.reference_versions import SHIPPED

    root = REFERENCE_DIR.parent.parent
    if shutil.which("git") is None or not (root / ".git").exists():
        pytest.skip("needs the git history")
    for f in sorted(REFERENCE_DIR.glob("*.csv")):
        current = hashlib.sha256(f.read_bytes()).hexdigest()
        rel = f.relative_to(root).as_posix()
        commits = subprocess.run(["git", "log", "--format=%H", "--", rel], cwd=root, capture_output=True,
                                 text=True).stdout.split()
        for commit in commits:
            blob = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=root, capture_output=True).stdout
            sha = hashlib.sha256(blob).hexdigest()
            assert sha == current or sha in SHIPPED.get(f.name, ()), f"{f.name} @ {commit[:7]} mangler i SHIPPED"
