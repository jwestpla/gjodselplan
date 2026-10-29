import shutil

import pytest

from gjodsel.config import REFERENCE_DIR
from gjodsel.reference import ReferenceDataError, is_unverified, load_reference


@pytest.fixture
def ref_dir(tmp_path):
    """A writable copy of the bundled reference CSVs."""
    target = tmp_path / "reference"
    shutil.copytree(REFERENCE_DIR, target)
    return target


def edit(ref_dir, file, old, new):
    path = ref_dir / file
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not found in {file}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def load_errors(ref_dir):
    with pytest.raises(ReferenceDataError) as exc:
        load_reference(ref_dir)
    return exc.value.errors


def assert_error(errors, *fragments):
    assert any(all(f in e for f in fragments) for e in errors), errors


# --- Bundled data -----------------------------------------------------------


def test_bundled_reference_loads():
    ref = load_reference(REFERENCE_DIR)
    assert set(ref.cultures) == {"eple", "paere", "sotkirsebaer", "surkirsebaer", "plomme"}
    assert len(ref.p_al_correction) == 8
    assert len(ref.k_correction) == 7 * 5
    assert {a.stage for a in ref.age_factors} == {"planteår", "ungtre", "bærende"}
    assert set(ref.soil_optimum) == {"ph", "p_al", "k_al", "mg_al", "ca_al"}


def test_decimal_comma_and_quoted_source_parsed():
    ref = load_reference(REFERENCE_DIR)
    paere = ref.cultures["paere"]
    assert paere.n_base == 6.5
    assert paere.source == "NIBIO GH tab.7; yield_basis/n_per_tonne ANTATT"
    assert ref.products["multi_k_mg"].k == 35.7
    assert ref.products["fg_12_4_18"].mg is None  # empty = unknown, not 0


def test_unverified_flags():
    ref = load_reference(REFERENCE_DIR)
    assert not ref.cultures["eple"].unverified
    assert ref.cultures["paere"].unverified
    assert ref.cultures["surkirsebaer"].unverified
    assert ref.products["kalksalpeter"].unverified  # SJEKK ETIKETT
    assert all(not row.unverified for row in ref.p_al_correction)
    files = {f for f, _ in ref.unverified_rows()}
    assert "k_correction.csv" in files
    assert "p_al_correction.csv" not in files


@pytest.mark.parametrize("source, expected", [
    ("ANTATT", True),
    ("NIBIO GH tab.7; yield_basis ANTATT", True),
    ("antatt", True),
    ("SJEKK ETIKETT", True),
    ("", True),
    (None, True),
    ("NIBIO GH tab.2", False),
])
def test_is_unverified(source, expected):
    assert is_unverified(source) is expected


def test_excel_style_file_accepted(ref_dir):
    # BOM, CRLF line endings and a trailing ';;;' row as Excel may write them.
    path = ref_dir / "soil_optimum.csv"
    text = path.read_text(encoding="utf-8-sig").replace("\n", "\r\n") + ";;;\r\n"
    path.write_text(text, encoding="utf-8-sig", newline="")
    assert load_reference(ref_dir).soil_optimum["ph"].min == 5.5


# --- Band coverage ------------------------------------------------------------


def test_gap_in_p_al_bands(ref_dir):
    edit(ref_dir, "p_al_correction.csv", "2,5;3,5;50", "2,6;3,5;50")
    assert_error(load_errors(ref_dir), "p_al_correction.csv", "hull", "2,5–2,6")


def test_overlap_in_p_al_bands(ref_dir):
    edit(ref_dir, "p_al_correction.csv", "2,5;3,5;50", "2,4;3,5;50")
    assert_error(load_errors(ref_dir), "p_al_correction.csv", "overlapp", "2,4–2,5")


def test_bands_must_start_at_zero(ref_dir):
    edit(ref_dir, "n_mold_correction.csv", "0;3;2;", "1;3;2;")
    assert_error(load_errors(ref_dir), "n_mold_correction.csv", "hull", "0–1")


def test_missing_band_row(ref_dir):
    edit(ref_dir, "n_mold_correction.csv", "4,5;12,5;0;NIBIO GH N-korreksjon\n", "")
    assert_error(load_errors(ref_dir), "n_mold_correction.csv", "hull", "4,5–12,5")


def test_overlap_in_age_ranges(ref_dir):
    edit(ref_dir, "age_factors.csv", "1;2;ungtre", "1;3;ungtre")
    assert_error(load_errors(ref_dir), "age_factors.csv", "overlapp", "3–3")


def test_gap_in_p_limit_years(ref_dir):
    edit(ref_dir, "p_limits.csv", "standard;2030;2032", "standard;2031;2032")
    assert_error(load_errors(ref_dir), "p_limits.csv (standard)", "hull", "2030–2030")


def test_standard_region_required(ref_dir):
    path = ref_dir / "p_limits.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("standard;", "std;"), encoding="utf-8")
    assert_error(load_errors(ref_dir), "regionen «standard» mangler")


# --- K grid ---------------------------------------------------------------------


def test_k_grid_missing_cell(ref_dir):
    path = ref_dir / "k_correction.csv"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    removed = next(l for l in lines if l.startswith("80;110;10,5;15,5;"))
    path.write_text("".join(l for l in lines if l != removed), encoding="utf-8")
    assert_error(load_errors(ref_dir), "mangler verdi", "K-HNO₃ 80–110", "K-AL 10,5–15,5")


def test_k_grid_overlapping_kal_band(ref_dir):
    edit(ref_dir, "k_correction.csv", "30;50;6,5;10,5;", "30;50;6,0;10,5;")
    errors = load_errors(ref_dir)
    assert_error(errors, "(K-AL)", "overlapp")


def test_k_missing_hno3_fallback_must_be_unique(ref_dir):
    path = ref_dir / "k_correction.csv"
    text = path.read_text(encoding="utf-8").replace(";1;NIBIO", ";0;NIBIO")
    path.write_text(text, encoding="utf-8")
    assert_error(load_errors(ref_dir), "nøyaktig ett K-HNO₃-intervall", "fant 0")


def test_k_missing_hno3_fallback_must_cover_whole_band(ref_dir):
    edit(ref_dir, "k_correction.csv", "50;80;0;6,5;50;1;", "50;80;0;6,5;50;0;")
    assert_error(load_errors(ref_dir), "alle rader i K-HNO₃ 50–80")


# --- Headers, types, cross references ----------------------------------------


def test_missing_column(ref_dir):
    edit(ref_dir, "p_al_correction.csv", "pal_min;pal_max;pct;source", "pal_min;pal_max;prosent;source")
    assert_error(load_errors(ref_dir), "p_al_correction.csv", "mangler kolonne", "pct")


def test_bad_number(ref_dir):
    edit(ref_dir, "cultures.csv", "eple;Eple;4,0;", "eple;Eple;fire;")
    assert_error(load_errors(ref_dir), "cultures.csv linje 2", "«n_base» = «fire» er ikke et tall")


def test_date_corrupted_by_excel(ref_dir):
    edit(ref_dir, "soil_optimum.csv", "ph;5,5;6,5;", "ph;05.mai;6,5;")
    assert_error(load_errors(ref_dir), "soil_optimum.csv", "«05.mai» er ikke et tall")


def test_required_value_missing(ref_dir):
    edit(ref_dir, "products.csv", "kalksalpeter;Kalksalpeter;15,5;", "kalksalpeter;Kalksalpeter;;")
    assert_error(load_errors(ref_dir), "products.csv linje 2", "«n» er tom")


def test_unquoted_semicolon_in_source(ref_dir):
    edit(ref_dir, "cultures.csv", '"NIBIO GH tab.7; yield_basis/n_per_tonne ANTATT"',
         "NIBIO GH tab.7; yield_basis/n_per_tonne ANTATT")
    assert_error(load_errors(ref_dir), "cultures.csv linje 3", "for mange felt")


def test_flag_must_be_0_or_1(ref_dir):
    edit(ref_dir, "age_factors.csv", "1,0;1,0;1,0;1;ANTATT", "1,0;1,0;1,0;2;ANTATT")
    assert_error(load_errors(ref_dir), "«apply_yield_adj» = «2» må være 0 eller 1")


def test_duplicate_code(ref_dir):
    edit(ref_dir, "cultures.csv", "plomme;Plomme;", "eple;Plomme;")
    assert_error(load_errors(ref_dir), "cultures.csv", "code «eple» finnes 2 ganger")


def test_template_unknown_product(ref_dir):
    edit(ref_dir, "templates.csv", "bærende;1;opti_ns_27;", "bærende;1;opti_ns_99;")
    assert_error(load_errors(ref_dir), "templates.csv", "«opti_ns_99» finnes ikke i products.csv")


def test_template_unknown_timing(ref_dir):
    edit(ref_dir, "templates.csv", ";etter_hosting;", ";etter_innhøsting;")
    assert_error(load_errors(ref_dir), "timing_code «etter_innhøsting» finnes ikke i timings.csv")


def test_missing_file(ref_dir):
    (ref_dir / "timings.csv").unlink()
    assert_error(load_errors(ref_dir), "timings.csv: filen mangler")


def test_non_utf8_file(ref_dir):
    path = ref_dir / "cultures.csv"
    path.write_bytes(path.read_text(encoding="utf-8-sig").encode("cp1252"))
    assert_error(load_errors(ref_dir), "cultures.csv", "UTF-8")


def test_all_errors_reported_together(ref_dir):
    edit(ref_dir, "p_al_correction.csv", "2,5;3,5;50", "2,6;3,5;50")
    edit(ref_dir, "cultures.csv", "eple;Eple;4,0;", "eple;Eple;fire;")
    errors = load_errors(ref_dir)
    assert_error(errors, "p_al_correction.csv", "hull")
    assert_error(errors, "cultures.csv", "fire")


# --- Product editor ---------------------------------------------------------------------------------


def test_save_products_roundtrip(ref_dir):
    from dataclasses import replace

    from gjodsel.reference import save_products

    ref = load_reference(ref_dir)
    products = list(ref.products.values())
    products[0] = replace(products[0], price_nok_per_kg=6.5, source="Etikett kontrollert 2026")
    new_ref = save_products(ref_dir, products)
    assert new_ref.products["kalksalpeter"].price_nok_per_kg == 6.5
    assert not new_ref.products["kalksalpeter"].unverified
    raw = (ref_dir / "products.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                    # BOM for Excel
    assert "kalksalpeter;Kalksalpeter;15,5;0;0;0;0;19;0;6,5;Etikett kontrollert 2026" in raw.decode("utf-8-sig")
    assert ";12;4;18;;;;;;" in raw.decode("utf-8-sig")       # unknown values stay empty


def test_save_products_restores_file_on_error(ref_dir):
    from gjodsel.reference import save_products

    before = (ref_dir / "products.csv").read_bytes()
    ref = load_reference(ref_dir)
    without_patentkali = [p for p in ref.products.values() if p.code != "patentkali"]  # used by a template
    with pytest.raises(ReferenceDataError) as exc:
        save_products(ref_dir, without_patentkali)
    assert any("«patentkali» finnes ikke i products.csv" in e for e in exc.value.errors)
    assert (ref_dir / "products.csv").read_bytes() == before
