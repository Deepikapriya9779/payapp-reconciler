import datetime as dt
from decimal import Decimal

import pytest
from conftest import CHANGE_ORDERS, CURRENT, PRIOR, SOV, SUB, replace_row, review, row

from payapp_reconciler.certify import certifiable_lines
from payapp_reconciler.contract import (
    contract_lines,
    contract_sum_to_date,
    is_change_order_item,
    retainage_rate,
)
from payapp_reconciler.models import ChangeOrder

D = Decimal
FEB = dt.date(2026, 2, 28)


def test_contract_lines_apply_approved_cos_only() -> None:
    lines = contract_lines(SOV, CHANGE_ORDERS, FEB)
    assert list(lines) == ["01", "02", "03", "CO-02", "CO-03"]
    assert lines["02"].scheduled_value == D("65000")  # CO-01 adjusts an existing line
    assert lines["CO-02"].scheduled_value == 0 and not lines["CO-02"].approved
    assert lines["CO-03"].is_change_order_line


def test_change_orders_count_as_of_their_approval_date() -> None:
    before = dt.date(2026, 1, 10)
    assert contract_lines(SOV, CHANGE_ORDERS, before)["02"].scheduled_value == D("60000")
    assert contract_sum_to_date(SUB, CHANGE_ORDERS, before) == D("100000")
    assert contract_sum_to_date(SUB, CHANGE_ORDERS, FEB) == D("105000")


def test_approved_date_requires_approved_status() -> None:
    with pytest.raises(ValueError, match="approved_date is set but status is pending"):
        ChangeOrder.model_validate(CHANGE_ORDERS[1].model_dump() | {"approved_date": "2026-01-01"})


@pytest.mark.parametrize(("pct", "rate"), [("49.99", "10"), ("50", "5"), ("80", "5")])
def test_retainage_rate_reduction(pct: str, rate: str) -> None:
    assert retainage_rate(SUB, D(pct)) == D(rate)


def test_is_change_order_item() -> None:
    assert is_change_order_item("CO-07") and is_change_order_item(" co-1")
    assert not is_change_order_item("07")


def test_certifiable_clean_application_matches_claim() -> None:
    cert = certifiable_lines(review())
    assert [line.total_completed_stored for line in cert.lines] == [
        r.total_completed_stored for r in CURRENT
    ]
    assert cert.pct_complete == D("32.38") and cert.retainage_rate == D("10")
    assert cert.total_retainage == D("3400.00")


def test_certifiable_rebuilds_d_and_caps_g_at_c() -> None:
    rows = replace_row(CURRENT, "02", row("02", "Ductwork", "70000", "15000", "60000"))
    [_, duct, _] = certifiable_lines(review(rows)).lines
    assert duct.scheduled_value == D("65000")
    assert duct.from_previous == D("12000")  # from the certificate, not the sub's 15,000
    assert duct.this_period == D("53000")  # cut so G = C
    assert duct.total_completed_stored == D("65000")


def test_certifiable_caps_stored_materials_when_e_is_exhausted() -> None:
    rows = replace_row(CURRENT, "03", row("03", "Equipment", "30000", "0", "0", f="45000"))
    equipment = certifiable_lines(review(rows)).lines[2]
    assert (equipment.this_period, equipment.materials_stored) == (0, D("30000"))


def test_certifiable_zeroes_unapproved_co_and_counts_duplicates_once() -> None:
    rows = [*CURRENT, CURRENT[1], row("CO-02", "Added VAV zone", "4000", "0", "4000")]
    cert = certifiable_lines(review(rows))
    assert [line.item_no for line in cert.lines] == ["01", "02", "03", "CO-02"]
    assert cert.lines[-1].total_completed_stored == 0
    assert cert.total_completed_stored == D("34000")


def test_certifiable_keeps_prior_work_on_lines_left_off_the_g703() -> None:
    cert = certifiable_lines(review([CURRENT[0], CURRENT[2]]))
    duct = next(line for line in cert.lines if line.item_no == "02")
    assert duct.total_completed_stored == PRIOR[1].work_completed == D("12000")
