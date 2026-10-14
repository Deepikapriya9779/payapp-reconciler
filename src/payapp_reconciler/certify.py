"""Rebuild what the GC can certify from trusted inputs instead of trusting the sub's arithmetic.

Policy (see docs/DESIGN.md):
- C comes from the SOV plus approved change orders as of the period end.
- D comes from the GC's prior certificate (D + E on that certificate).
- E and F are accepted as claimed. Verifying progress in the field is the PM's job.
- G is capped at C (E is cut first, then F).
- Lines for unapproved or unknown change orders certify at $0. Duplicate rows count once.
- Retainage is recomputed per line at the rate the subcontract calls for.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .contract import contract_lines, contract_sum_to_date, retainage_rate
from .loaders import ReviewInput
from .models import CertifiableLine
from .money import ZERO, apply_pct, pct_of


@dataclass(frozen=True)
class CertifiableLines:
    lines: list[CertifiableLine]
    contract_sum_to_date: Decimal
    pct_complete: Decimal  # certifiable total completed & stored / contract sum to date
    retainage_rate: Decimal

    @property
    def total_completed_stored(self) -> Decimal:
        return sum((line.total_completed_stored for line in self.lines), ZERO)

    @property
    def total_retainage(self) -> Decimal:
        return sum((line.retainage for line in self.lines), ZERO)


def prior_work_completed(review: ReviewInput) -> dict[str, Decimal]:
    """Column D the sub should show on each line: the prior certificate's D + E."""
    prior: dict[str, Decimal] = {}
    for row in review.prior_certified or []:
        prior[row.item_no] = prior.get(row.item_no, ZERO) + row.work_completed
    return prior


def _cap_to_scheduled_value(
    c: Decimal, d: Decimal, e: Decimal, f: Decimal
) -> tuple[Decimal, Decimal]:
    """Reduce E, then F, so that D + E + F does not exceed C."""
    if d + e + f <= c:
        return e, f
    e_cap = max(min(e, c - d - f), ZERO)
    f_cap = min(f, max(c - d - e_cap, ZERO))
    return e_cap, f_cap


def certifiable_lines(review: ReviewInput) -> CertifiableLines:
    as_of = review.period_to
    contract = contract_lines(review.sov, review.change_orders, as_of)
    prior = prior_work_completed(review)

    gross: list[tuple[str, str, Decimal, Decimal, Decimal, Decimal]] = []
    seen: set[str] = set()
    for row in review.submitted:
        if row.item_no in seen:  # duplicate row: counted once
            continue
        seen.add(row.item_no)
        line = contract.get(row.item_no)
        d = prior.get(row.item_no, ZERO)
        if line is None or not line.approved:
            # Unknown line or a change order that isn't approved: nothing new is certifiable.
            c = line.scheduled_value if line else ZERO
            e = f = ZERO
        else:
            c = line.scheduled_value
            e, f = _cap_to_scheduled_value(c, d, row.this_period, row.materials_stored)
        gross.append((row.item_no, row.description, c, d, e, f))

    # Lines the sub left off the G703 still carry the work certified on earlier applications.
    for item_no, line in contract.items():
        if item_no not in seen and line.approved:
            d = prior.get(item_no, ZERO)
            gross.append((item_no, line.description, line.scheduled_value, d, ZERO, ZERO))

    contract_to_date = contract_sum_to_date(review.subcontract, review.change_orders, as_of)
    total_g = sum((d + e + f for *_, d, e, f in gross), ZERO)
    pct = pct_of(total_g, contract_to_date)
    rate = retainage_rate(review.subcontract, pct)

    lines = [
        CertifiableLine(
            item_no=item_no,
            description=description,
            scheduled_value=c,
            from_previous=d,
            this_period=e,
            materials_stored=f,
            total_completed_stored=d + e + f,
            retainage=apply_pct(d + e + f, rate),
        )
        for item_no, description, c, d, e, f in gross
    ]
    return CertifiableLines(
        lines=lines, contract_sum_to_date=contract_to_date, pct_complete=pct, retainage_rate=rate
    )


def certifiable_g702(review: ReviewInput, cert: CertifiableLines) -> dict[int, Decimal]:
    """G702 lines 1-9 as the GC would fill them in from the certifiable G703."""
    l1 = review.subcontract.original_contract_sum
    l3 = cert.contract_sum_to_date
    l4 = cert.total_completed_stored
    l5 = cert.total_retainage
    l6 = l4 - l5
    # Line 7 is line 6 of the GC's previous certificate, not the sub's previous request.
    l7 = sum((r.total_completed_stored - r.retainage for r in review.prior_certified or []), ZERO)
    return {1: l1, 2: l3 - l1, 3: l3, 4: l4, 5: l5, 6: l6, 7: l7, 8: l6 - l7, 9: l3 - l6}
