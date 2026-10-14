from decimal import Decimal

import pytest

from payapp_reconciler.money import (
    apply_pct,
    differs,
    fmt_money,
    parse_money,
    pct_of,
    round_half_up,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234.56", "1234.56"),
        ("$1,234.56", "1234.56"),
        (" 12 ", "12.00"),
        (1500, "1500.00"),
        (Decimal("0.005"), "0.01"),
        ("-250.5", "-250.50"),
    ],
)
def test_parse_money(raw: object, expected: str) -> None:
    assert parse_money(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [1.1, True, "", "abc", "NaN", None])
def test_parse_money_rejects_bad_values(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_money(raw)


def test_round_half_up_not_bankers() -> None:
    # Python's default rounding would give 0.02 for 0.025; G703 spreadsheets give 0.03.
    assert round_half_up(Decimal("0.025")) == Decimal("0.03")
    assert round_half_up(Decimal("-0.025")) == Decimal("-0.03")


def test_pct_and_retainage_helpers() -> None:
    assert pct_of(Decimal("1"), Decimal("3")) == Decimal("33.33")
    assert pct_of(Decimal("5"), Decimal("0")) == Decimal("0.00")
    assert apply_pct(Decimal("12345.67"), Decimal("10")) == Decimal("1234.57")


def test_differs_allows_one_cent_of_slack() -> None:
    assert not differs(Decimal("100.00"), Decimal("100.01"))
    assert differs(Decimal("100.00"), Decimal("100.02"))


def test_fmt_money() -> None:
    assert fmt_money(Decimal("1234567.8")) == "$1,234,567.80"
    assert fmt_money(Decimal("-42")) == "-$42.00"
