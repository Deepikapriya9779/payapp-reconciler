"""The trusted side of the contract: scheduled values the GC recognizes and the retainage rate.

Everything here comes from the subcontract, the SOV and the change order log, never from the
sub's pay application.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .models import ChangeOrder, SovLine, Subcontract


def is_change_order_item(item_no: str) -> bool:
    """G703 convention: change-order lines carry item numbers like "CO-03"."""
    return item_no.strip().upper().startswith("CO")


@dataclass(frozen=True)
class ContractLine:
    item_no: str
    description: str
    scheduled_value: Decimal  # SOV value plus approved COs assigned to this line
    is_change_order_line: bool  # True when the line exists only because of change orders
    approved: bool  # a CO line with no approved CO behind it cannot be billed
    change_orders: tuple[ChangeOrder, ...] = ()

    def unapproved_change_orders(self, as_of: dt.date) -> list[ChangeOrder]:
        return [co for co in self.change_orders if not co.approved_as_of(as_of)]


def approved_change_orders(change_orders: list[ChangeOrder], as_of: dt.date) -> list[ChangeOrder]:
    return [co for co in change_orders if co.approved_as_of(as_of)]


def net_change_by_change_orders(change_orders: list[ChangeOrder], as_of: dt.date) -> Decimal:
    return sum((co.amount for co in approved_change_orders(change_orders, as_of)), Decimal("0"))


def contract_sum_to_date(
    subcontract: Subcontract, change_orders: list[ChangeOrder], as_of: dt.date
) -> Decimal:
    return subcontract.original_contract_sum + net_change_by_change_orders(change_orders, as_of)


def contract_lines(
    sov: list[SovLine], change_orders: list[ChangeOrder], as_of: dt.date
) -> dict[str, ContractLine]:
    """Every line the GC recognizes, keyed by item number, in SOV order then CO order."""
    lines = {
        s.line_no: ContractLine(
            item_no=s.line_no,
            description=s.description,
            scheduled_value=s.scheduled_value,
            is_change_order_line=False,
            approved=True,
        )
        for s in sov
    }
    by_line: dict[str, list[ChangeOrder]] = defaultdict(list)
    for co in change_orders:
        by_line[co.sov_line_no].append(co)

    for line_no, cos in by_line.items():
        approved_amount = sum((co.amount for co in cos if co.approved_as_of(as_of)), Decimal("0"))
        any_approved = any(co.approved_as_of(as_of) for co in cos)
        base = lines.get(line_no)
        if base is not None:  # CO adjusts an existing SOV line
            lines[line_no] = ContractLine(
                item_no=line_no,
                description=base.description,
                scheduled_value=base.scheduled_value + approved_amount,
                is_change_order_line=False,
                approved=True,
                change_orders=tuple(cos),
            )
        else:  # CO carried as its own G703 line
            lines[line_no] = ContractLine(
                item_no=line_no,
                description=cos[0].description,
                scheduled_value=approved_amount,
                is_change_order_line=True,
                approved=any_approved,
                change_orders=tuple(cos),
            )
    return lines


def retainage_rate(subcontract: Subcontract, pct_complete: Decimal) -> Decimal:
    """Contract retainage rate, dropping to the reduced rate once the threshold is reached."""
    reduced = subcontract.retainage_reduced_pct
    threshold = subcontract.retainage_reduction_threshold_pct
    if reduced is not None and threshold is not None and pct_complete >= threshold:
        return reduced
    return subcontract.retainage_pct
