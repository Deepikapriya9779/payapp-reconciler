"""Money and percent helpers. Every rounding in the project goes through `round_half_up`."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


def round_half_up(value: Decimal, places: Decimal = CENT) -> Decimal:
    """Round to `places` (cents by default) the way a G703 spreadsheet does."""
    return value.quantize(places, rounding=ROUND_HALF_UP)


def to_cents(value: Decimal) -> Decimal:
    return round_half_up(value, CENT)


def parse_decimal(raw: object) -> Decimal:
    """Parse "$1,234.56", "1234.56", 1234 or Decimal("1234.56") into Decimal.

    Floats are rejected outright: a binary float has already lost the cents.
    """
    if isinstance(raw, bool | float):
        raise ValueError(f"refusing float/bool value {raw!r}; pass money as a string or Decimal")
    if isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, str):
        text = raw.strip().replace("$", "").replace(",", "").replace("%", "")
        if not text:
            raise ValueError("value is blank")
        try:
            value = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"not a number: {raw!r}") from None
    else:
        raise ValueError(f"unsupported type {type(raw).__name__}")
    if not value.is_finite():
        raise ValueError(f"not a finite number: {raw!r}")
    return value


def parse_money(raw: object) -> Decimal:
    return to_cents(parse_decimal(raw))


def pct_of(part: Decimal, whole: Decimal) -> Decimal:
    """`part` as a percent of `whole`, two decimals (45.23 means 45.23%). Zero when whole is 0."""
    if whole == 0:
        return ZERO
    return round_half_up(part / whole * HUNDRED)


def apply_pct(amount: Decimal, pct: Decimal) -> Decimal:
    """`pct` percent of `amount`, rounded to cents (e.g. retainage on one G703 line)."""
    return to_cents(amount * pct / HUNDRED)


def differs(a: Decimal, b: Decimal, tolerance: Decimal = CENT) -> bool:
    """True when two amounts differ by more than rounding slack."""
    return abs(a - b) > tolerance


def fmt_money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(to_cents(value)):,.2f}"


def fmt_pct(value: Decimal) -> str:
    """10 -> "10%", 52.30 -> "52.3%", 33.33 -> "33.33%"."""
    text = f"{round_half_up(value):.2f}".rstrip("0").rstrip(".")
    return f"{text}%"
