"""Small in-memory pay applications for unit tests. No file I/O.

The base scenario is a clean application 2 on a $100,000 subcontract (10% retainage, 5% after
50% complete) with one approved CO on an existing line, one pending CO and one rejected CO.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from payapp_reconciler.loaders import ReviewInput
from payapp_reconciler.models import (
    ChangeOrder,
    COStatus,
    Finding,
    G702Summary,
    G703Row,
    ProjectInfo,
    SovLine,
    Subcontract,
)
from payapp_reconciler.money import ZERO, apply_pct, pct_of
from payapp_reconciler.rules import run_rules

D = Decimal
NET_CHANGE = D("5000")
PERIOD_1 = dt.date(2026, 1, 31)
PERIOD_2 = dt.date(2026, 2, 28)

SUB = Subcontract(
    sub_id="t",
    sub_name="Test Mechanical",
    trade="Mechanical",
    original_contract_sum=D("100000"),
    retainage_pct=D("10"),
    retainage_reduced_pct=D("5"),
    retainage_reduction_threshold_pct=D("50"),
)
SOV = [
    SovLine(sub_id="t", line_no="01", description="General Conditions", scheduled_value=D("10000")),
    SovLine(sub_id="t", line_no="02", description="Ductwork", scheduled_value=D("60000")),
    SovLine(sub_id="t", line_no="03", description="Equipment", scheduled_value=D("30000")),
]
CHANGE_ORDERS = [
    ChangeOrder(
        co_no="CO-01",
        sub_id="t",
        description="Added exhaust duct",
        amount=D("5000"),
        status=COStatus.APPROVED,
        sov_line_no="02",
        approved_date=dt.date(2026, 1, 15),
    ),
    ChangeOrder(
        co_no="CO-02",
        sub_id="t",
        description="Added VAV zone",
        amount=D("4000"),
        status=COStatus.PENDING,
        sov_line_no="CO-02",
    ),
    ChangeOrder(
        co_no="CO-03",
        sub_id="t",
        description="Upsized RTU",
        amount=D("2000"),
        status=COStatus.REJECTED,
        sov_line_no="CO-03",
    ),
]


def row(
    item: str,
    desc: str,
    c: str,
    d: str,
    e: str,
    f: str = "0",
    rate: str = "10",
) -> G703Row:
    """A G703 row whose math ties out."""
    g = D(d) + D(e) + D(f)
    return G703Row(
        item_no=item,
        description=desc,
        scheduled_value=D(c),
        from_previous=D(d),
        this_period=D(e),
        materials_stored=D(f),
        total_completed_stored=g,
        pct_complete=pct_of(g, D(c)),
        balance_to_finish=D(c) - g,
        retainage=apply_pct(g, D(rate)),
    )


PRIOR = [
    row("01", "General Conditions", "10000", "0", "2000"),
    row("02", "Ductwork", "65000", "0", "12000"),
    row("03", "Equipment", "30000", "0", "0"),
]
CURRENT = [
    row("01", "General Conditions", "10000", "2000", "2000"),
    row("02", "Ductwork", "65000", "12000", "18000"),
    row("03", "Equipment", "30000", "0", "0"),
]


def g702_for(
    rows: list[G703Row],
    prior: list[G703Row] | None,
    app_no: int,
    period: dt.date,
    net_change: Decimal = NET_CHANGE,
) -> G702Summary:
    """A G702 that ties exactly to `rows` and the prior certificate."""
    total_g = sum((r.total_completed_stored for r in rows), ZERO)
    total_i = sum((r.retainage for r in rows), ZERO)
    previous = sum((r.total_completed_stored - r.retainage for r in prior or []), ZERO)
    contract = SUB.original_contract_sum + net_change
    earned = total_g - total_i
    return G702Summary(
        sub_id="t",
        application_no=app_no,
        period_to=period,
        original_contract_sum=SUB.original_contract_sum,
        net_change_by_change_orders=net_change,
        contract_sum_to_date=contract,
        total_completed_and_stored=total_g,
        retainage=total_i,
        total_earned_less_retainage=earned,
        less_previous_certificates=previous,
        current_payment_due=earned - previous,
        balance_to_finish_including_retainage=contract - earned,
    )


def review(
    rows: list[G703Row] | None = None,
    prior: list[G703Row] | None = PRIOR,
    app_no: int = 2,
    period: dt.date = PERIOD_2,
    g702: G702Summary | None = None,
    subcontract: Subcontract = SUB,
) -> ReviewInput:
    rows = CURRENT if rows is None else rows
    return ReviewInput(
        project_dir=Path("."),
        project=ProjectInfo(name="Test Project", general_contractor="Test GC"),
        app_no=app_no,
        subcontract=subcontract,
        sov=SOV,
        change_orders=CHANGE_ORDERS,
        prior_certified=prior,
        submitted=rows,
        g702=g702 or g702_for(rows, prior, app_no, period),
    )


def replace_row(rows: list[G703Row], item: str, new: G703Row) -> list[G703Row]:
    return [new if r.item_no == item else r for r in rows]


def with_g702(r: ReviewInput, **changes: Decimal) -> ReviewInput:
    return dataclasses.replace(r, g702=r.g702.model_copy(update=changes))


def findings(r: ReviewInput, rule_id: str | None = None) -> list[Finding]:
    return [f for f in run_rules(r) if rule_id is None or f.rule_id == rule_id]


@pytest.fixture
def clean() -> ReviewInput:
    return review()
