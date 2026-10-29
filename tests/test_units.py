import pytest

from gjodsel.units import (
    format_number,
    format_plain,
    k2o_to_k,
    k_to_k2o,
    p2o5_to_p,
    p_to_p2o5,
    parse_number,
    round_half_up,
)


def test_p2o5_to_p_fertilab_case():
    # PLAN §10 phase 2: 57 mg P2O5/100 g -> 24.9 mg P/100 g
    assert round_half_up(p2o5_to_p(57), 1) == 24.9


def test_oxide_round_trip():
    assert p_to_p2o5(p2o5_to_p(12.3)) == pytest.approx(12.3)
    assert k_to_k2o(k2o_to_k(40)) == pytest.approx(40)
    assert k2o_to_k(43) == pytest.approx(35.69, abs=0.01)


@pytest.mark.parametrize("text, expected", [
    ("10,8", 10.8),
    ("10.8", 10.8),
    (" 10,8 ", 10.8),
    ("-0,5", -0.5),
    ("−0,5", -0.5),       # Unicode minus
    ("1 234,5", 1234.5),       # space as thousands separator
    ("1 234,5", 1234.5),  # non-breaking space
    ("3", 3.0),
    (2.5, 2.5),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("text", ["", "   ", None])
def test_parse_number_empty_is_none(text):
    assert parse_number(text) is None


@pytest.mark.parametrize("text", ["abc", "1,2,3", "1.234,5", "nan", "inf", "06.mai"])
def test_parse_number_rejects(text):
    with pytest.raises(ValueError):
        parse_number(text)


def test_round_half_up_not_bankers():
    assert round_half_up(0.25, 1) == 0.3
    assert round_half_up(0.375, 1) == 0.4
    assert round_half_up(2.675, 2) == 2.68
    assert round_half_up(-0.25, 1) == -0.3


def test_format_plain():
    assert format_plain(4.0) == "4"
    assert format_plain(24.8748) == "24,8748"
    assert format_plain(12) == "12"
    assert format_plain(None) == ""


def test_format_number():
    assert format_number(10.8) == "10,8"
    assert format_number(0.375) == "0,4"
    assert format_number(7) == "7,0"
    assert format_number(1234.5, 0) == "1235"
    assert format_number(-0.04) == "0,0"  # no "-0,0"
    assert format_number(None) == ""
    assert format_number(None, empty="–") == "–"
