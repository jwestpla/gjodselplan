import pytest

from gjodsel.validation import (
    is_valid_email,
    is_valid_kommunenr,
    is_valid_org_nr,
    is_valid_phone,
    is_valid_postcode,
    normalize_phone,
)


@pytest.mark.parametrize("value", [
    "923609016",     # sum 126, rest 5 -> check 6
    "974760673",     # sum 173, rest 8 -> check 3
    "923 609 016",
    "310000000",     # sum 11, rest 0 -> check 11 -> 0
])
def test_valid_org_nr(value):
    assert is_valid_org_nr(value)


@pytest.mark.parametrize("value", [
    "923609017",     # wrong check digit
    "92360901",      # 8 digits
    "9236090160",    # 10 digits
    "92360901a",
    "",
    None,
])
def test_invalid_org_nr(value):
    assert not is_valid_org_nr(value)


@pytest.mark.parametrize("last", range(10))
def test_org_nr_check_digit_10_is_never_valid(last):
    # 4 * 3 = 12, rest 1 -> check digit would be 10, so no number is valid
    assert not is_valid_org_nr(f"40000000{last}")


def test_postcode():
    assert is_valid_postcode("0150")
    assert is_valid_postcode(" 1430 ")
    assert not is_valid_postcode("150")
    assert not is_valid_postcode("14300")
    assert not is_valid_postcode("14a0")


@pytest.mark.parametrize("value", ["12345678", "123 45 678", "+47 12345678", "004712345678"])
def test_valid_phone(value):
    assert is_valid_phone(value)
    assert normalize_phone(value) == "12345678"


@pytest.mark.parametrize("value", ["1234567", "123456789", "+46 12345678", "abcdefgh", ""])
def test_invalid_phone(value):
    assert not is_valid_phone(value)


def test_email():
    assert is_valid_email("ola@gard.no")
    assert is_valid_email(" kari.nordmann@nlr.no ")
    assert not is_valid_email("ola@gard")
    assert not is_valid_email("ola gard@x.no")
    assert not is_valid_email("")


def test_kommunenr():
    assert is_valid_kommunenr("4640")
    assert is_valid_kommunenr("3401")
    assert not is_valid_kommunenr("464")
    assert not is_valid_kommunenr("0000")
    assert not is_valid_kommunenr("46a0")
