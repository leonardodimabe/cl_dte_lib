import pytest

from dte_chile.rut import Rut, format_rut, validate_rut


@pytest.mark.parametrize(
    "value,expected",
    [
        ("76.192.083-9", "76192083-9"),
        ("76192083-9", "76192083-9"),
        ("761920839", "76192083-9"),
        ("5.126.663-3", "5126663-3"),
        ("11.111.111-1", "11111111-1"),
    ],
)
def test_format_rut_valid(value, expected):
    assert format_rut(value) == expected


@pytest.mark.parametrize("value", ["76192083-K", "12345678-0", "abc", "1"])
def test_rut_invalid(value):
    assert validate_rut(value) is False


def test_rut_with_dv_k():
    # RUT cuyo dígito verificador es K (resto módulo 11 == 10):
    assert validate_rut("17.099.910-K") is True
    assert format_rut("17099910k") == "17099910-K"


def test_rut_dataclass_normalizes():
    assert Rut("76.192.083-9").value == "76192083-9"
    assert str(Rut("761920839")) == "76192083-9"


def test_rut_dataclass_rejects_invalid():
    with pytest.raises(ValueError):
        Rut("12345678-0")
