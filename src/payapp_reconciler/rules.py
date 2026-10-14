"""The ten review rules.

Each rule is a generator registered with `@rule(id, name)`. It receives a `ReviewContext` and a
`finding` factory with the rule id and name already filled in, and yields `Finding`s.

`dollar_impact` is the effect on what the sub asked to be paid, gross of retainage:
positive = over-claimed, negative = under-claimed, 0 = no direct effect on the payment.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal

from .certify import CertifiableLines, certifiable_lines, prior_work_completed
from .contract import (
    ContractLine,
    contract_lines,
    is_change_order_item,
    net_change_by_change_orders,
)
from .loaders import ReviewInput
from .models import G702_LINES, ChangeOrder, Finding, G703Row, ReviewConfig, Severity
from .money import ZERO, apply_pct, differs, fmt_money, fmt_pct, pct_of

FindingFactory = Callable[..., Finding]
RuleCheck = Callable[["ReviewContext", FindingFactory], Iterator[Finding]]


@dataclass(frozen=True)
class ReviewContext:
    review: ReviewInput
    config: ReviewConfig
    contract: dict[str, ContractLine]
    prior_work: dict[str, Decimal]  # item_no -> prior certificate D + E
    prior_rows: dict[str, G703Row]
    certifiable: CertifiableLines
    rows: list[G703Row]  # submitted rows, first occurrence of each item number

    def differs(self, a: Decimal, b: Decimal) -> bool:
        return differs(a, b, self.config.tolerance)


def build_context(review: ReviewInput, config: ReviewConfig | None = None) -> ReviewContext:
    unique: dict[str, G703Row] = {}
    for row in review.submitted:
        unique.setdefault(row.item_no, row)
    return ReviewContext(
        review=review,
        config=config or ReviewConfig(),
        contract=contract_lines(review.sov, review.change_orders, review.period_to),
        prior_work=prior_work_completed(review),
        prior_rows={row.item_no: row for row in review.prior_certified or []},
        certifiable=certifiable_lines(review),
        rows=list(unique.values()),
    )


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    check: RuleCheck


RULES: list[Rule] = []


def rule(rule_id: str, name: str) -> Callable[[RuleCheck], RuleCheck]:
    def register(check: RuleCheck) -> RuleCheck:
        RULES.append(Rule(rule_id, name, check))
        return check

    return register


def run_rules(review: ReviewInput, config: ReviewConfig | None = None) -> list[Finding]:
    return apply_rules(build_context(review, config))


def apply_rules(ctx: ReviewContext) -> list[Finding]:
    findings: list[Finding] = []
    for r in RULES:
        make = functools.partial(Finding, rule_id=r.rule_id, rule_name=r.name)
        findings.extend(r.check(ctx, make))
    return findings


def _line(row: G703Row) -> str:
    return f"Line {row.item_no} ({row.description})"


def _co_status(co: ChangeOrder) -> str:
    if co.status.value == "approved":  # approved, but after this period closed
        return f"{co.co_no} was approved {co.approved_date}, after this period"
    return f"{co.co_no} is {co.status.value}"


# ---------------------------------------------------------------- R01-R10


@rule("R01", "Row math")
def check_row_math(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    for row in ctx.rows:
        g = row.from_previous + row.this_period + row.materials_stored
        if ctx.differs(row.total_completed_stored, g):
            yield finding(
                severity=Severity.ERROR,
                item_no=row.item_no,
                column="G",
                expected=g,
                actual=row.total_completed_stored,
                dollar_impact=row.total_completed_stored - g,
                message=f"{_line(row)}: G should be D + E + F = {fmt_money(g)}, "
                f"but shows {fmt_money(row.total_completed_stored)}.",
            )
        h = row.scheduled_value - row.total_completed_stored
        if ctx.differs(row.balance_to_finish, h):
            yield finding(
                severity=Severity.ERROR,
                item_no=row.item_no,
                column="H",
                expected=h,
                actual=row.balance_to_finish,
                message=f"{_line(row)}: balance to finish should be C - G = {fmt_money(h)}, "
                f"but shows {fmt_money(row.balance_to_finish)}.",
            )
        pct = pct_of(row.total_completed_stored, row.scheduled_value)
        if abs(row.pct_complete - pct) > Decimal("0.01"):
            yield finding(
                severity=Severity.INFO,
                item_no=row.item_no,
                column="pct",
                expected=pct,
                actual=row.pct_complete,
                message=f"{_line(row)}: % complete should be G / C = {fmt_pct(pct)}, "
                f"but shows {fmt_pct(row.pct_complete)}.",
            )


@rule("R02", "Scheduled value")
def check_scheduled_value(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    for row in ctx.rows:
        line = ctx.contract.get(row.item_no)
        if line is None or not line.approved:
            continue  # unknown and unapproved lines belong to R05 / R10
        if not ctx.differs(row.scheduled_value, line.scheduled_value):
            continue
        message = (
            f"{_line(row)}: scheduled value shows {fmt_money(row.scheduled_value)}; the SOV plus "
            f"approved change orders give {fmt_money(line.scheduled_value)}."
        )
        unapproved = line.unapproved_change_orders(ctx.review.period_to)
        unapproved_total = sum((co.amount for co in unapproved), ZERO)
        if unapproved and not ctx.differs(
            row.scheduled_value - line.scheduled_value, unapproved_total
        ):
            names = ", ".join(_co_status(co) for co in unapproved)
            message += f" The difference is the unapproved change order amount ({names})."
        yield finding(
            severity=Severity.ERROR,
            item_no=row.item_no,
            column="C",
            expected=line.scheduled_value,
            actual=row.scheduled_value,
            message=message,
        )


@rule("R03", "From previous application")
def check_from_previous(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    app_no = ctx.review.app_no
    for row in ctx.rows:
        expected = ctx.prior_work.get(row.item_no, ZERO)
        if not ctx.differs(row.from_previous, expected):
            continue
        if app_no == 1:
            message = (
                f"{_line(row)}: application 1 should show $0.00 from previous, "
                f"but shows {fmt_money(row.from_previous)}."
            )
        else:
            message = (
                f"{_line(row)}: from previous shows {fmt_money(row.from_previous)}, but the GC's "
                f"certificate for application {app_no - 1} shows {fmt_money(expected)} "
                f"completed (D + E). Carry forward the certified amount, not your own billing."
            )
            prior = ctx.prior_rows.get(row.item_no)
            if (
                prior is not None
                and prior.materials_stored > 0
                and not ctx.differs(row.from_previous - expected, prior.materials_stored)
            ):
                message += (
                    " The difference equals last month's stored materials; once installed, "
                    "they are billed in E (this period), not D."
                )
        yield finding(
            severity=Severity.ERROR,
            item_no=row.item_no,
            column="D",
            expected=expected,
            actual=row.from_previous,
            dollar_impact=row.from_previous - expected,
            message=message,
        )


@rule("R04", "Over-billing")
def check_over_billing(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    for row in ctx.rows:
        line = ctx.contract.get(row.item_no)
        if line is None or not line.approved:
            continue
        over = row.total_completed_stored - line.scheduled_value
        if over > ctx.config.tolerance:
            pct = pct_of(row.total_completed_stored, line.scheduled_value)
            yield finding(
                severity=Severity.ERROR,
                item_no=row.item_no,
                column="G",
                expected=line.scheduled_value,
                actual=row.total_completed_stored,
                dollar_impact=over,
                message=f"{_line(row)}: billed {fmt_money(row.total_completed_stored)} "
                f"({fmt_pct(pct)}) against an approved scheduled value of "
                f"{fmt_money(line.scheduled_value)}. Work beyond the scheduled value needs "
                f"an approved change order.",
            )


@rule("R05", "Unapproved change order")
def check_unapproved_change_orders(
    ctx: ReviewContext, finding: FindingFactory
) -> Iterator[Finding]:
    for row in ctx.rows:
        line = ctx.contract.get(row.item_no)
        if line is not None and line.approved:
            continue
        if line is None and not is_change_order_item(row.item_no):
            continue  # an extra non-CO line is an SOV problem (R10)
        if line is None:
            status = f"{row.item_no} is not in the change order log"
        else:
            status = "; ".join(_co_status(co) for co in line.change_orders)
        billed = row.total_completed_stored
        if billed:
            message = (
                f"{_line(row)}: {status}. Billing of {fmt_money(billed)} can't be certified "
                f"until the change order is approved."
            )
        else:
            message = f"{_line(row)}: {status}. Remove the line until the change order is approved."
        yield finding(
            severity=Severity.ERROR if billed else Severity.WARNING,
            item_no=row.item_no,
            column="G",
            expected=ZERO,
            actual=billed,
            dollar_impact=billed,
            message=message,
        )


@rule("R06", "Retainage rate")
def check_retainage(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    rate = ctx.certifiable.retainage_rate
    rows = ctx.rows
    mismatched = [
        r for r in rows if ctx.differs(r.retainage, apply_pct(r.total_completed_stored, rate))
    ]
    if not mismatched:
        return

    sub = ctx.review.subcontract
    threshold = sub.retainage_reduction_threshold_pct
    pct = ctx.certifiable.pct_complete
    other_rates = []
    if sub.retainage_reduced_pct is not None and threshold is not None:
        other_rates = [x for x in (sub.retainage_pct, sub.retainage_reduced_pct) if x != rate]
    for alt in other_rates:
        if any(ctx.differs(r.retainage, apply_pct(r.total_completed_stored, alt)) for r in rows):
            continue
        # The whole application uses the other contract rate: report it once.
        claimed = sum((r.retainage for r in rows), ZERO)
        expected = sum((apply_pct(r.total_completed_stored, rate) for r in rows), ZERO)
        if alt > rate:
            message = (
                f"Retainage is held at {fmt_pct(alt)}, but the subcontract reduces it to "
                f"{fmt_pct(rate)} once the work is {fmt_pct(threshold)} complete, and "
                f"certifiable work is {fmt_pct(pct)} complete. Retainage is overstated by "
                f"{fmt_money(claimed - expected)}."
            )
        else:
            message = (
                f"Retainage was reduced to {fmt_pct(alt)} early: the reduction applies at "
                f"{fmt_pct(threshold)} complete, and certifiable work is {fmt_pct(pct)} "
                f"complete. Retainage is understated by {fmt_money(expected - claimed)}."
            )
        yield finding(
            severity=Severity.ERROR,
            column="I",
            expected=expected,
            actual=claimed,
            dollar_impact=expected - claimed,
            message=message,
        )
        return

    for r in mismatched:
        expected = apply_pct(r.total_completed_stored, rate)
        yield finding(
            severity=Severity.ERROR,
            item_no=r.item_no,
            column="I",
            expected=expected,
            actual=r.retainage,
            dollar_impact=expected - r.retainage,
            message=f"{_line(r)}: retainage should be {fmt_pct(rate)} of G = "
            f"{fmt_money(expected)}, but shows {fmt_money(r.retainage)}.",
        )


@rule("R07", "Stored materials backup")
def check_stored_materials(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    for row in ctx.rows:
        if row.materials_stored > 0:
            yield finding(
                severity=Severity.WARNING,
                item_no=row.item_no,
                column="F",
                actual=row.materials_stored,
                message=f"{_line(row)}: {fmt_money(row.materials_stored)} in stored materials. "
                "Before certifying, get the supplier invoice, a bill of lading or delivery "
                "ticket, and proof the material is insured and stored securely (plus title "
                "and location documents if it is stored off site).",
            )


@rule("R08", "G702 ties to G703")
def check_g702(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    review = ctx.review
    g = review.g702
    as_of = review.period_to
    prior = review.prior_certified or []
    prior_line_6 = sum((r.total_completed_stored - r.retainage for r in prior), ZERO)
    previous = (
        f"line 6 of the GC's certificate for application {review.app_no - 1}"
        if prior
        else "nothing on application 1"
    )
    # Source lines are compared to their source; derived lines to the sub's own upstream lines,
    # so a single keying error is reported once, on the line where it happened.
    expected: dict[int, tuple[Decimal, str]] = {
        1: (review.subcontract.original_contract_sum, "the subcontract"),
        2: (
            net_change_by_change_orders(review.change_orders, as_of),
            f"change orders approved as of {as_of}; pending, rejected and unlogged COs don't count",
        ),
        3: (g.line(1) + g.line(2), "line 1 + line 2"),
        4: (sum((r.total_completed_stored for r in review.submitted), ZERO), "the G703 G total"),
        5: (sum((r.retainage for r in review.submitted), ZERO), "the G703 column I total"),
        6: (g.line(4) - g.line(5), "line 4 - line 5"),
        7: (prior_line_6, previous),
        8: (g.line(6) - g.line(7), "line 6 - line 7"),
        9: (g.line(3) - g.line(6), "line 3 - line 6"),
    }
    payment_sign = {4: 1, 5: -1, 6: 1, 7: -1, 8: 1}
    for number, (value, source) in expected.items():
        claimed = g.line(number)
        if not ctx.differs(claimed, value):
            continue
        label = G702_LINES[number][1]
        yield finding(
            severity=Severity.ERROR,
            column=f"L{number}",
            expected=value,
            actual=claimed,
            dollar_impact=payment_sign.get(number, 0) * (claimed - value),
            message=f"G702 line {number} ({label}) shows {fmt_money(claimed)} but should be "
            f"{fmt_money(value)} ({source}).",
        )


@rule("R09", "Front-loading")
def check_front_loading(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    keywords = [k.lower() for k in ctx.config.front_load_keywords]
    threshold = ctx.config.front_load_threshold_pts
    for row in ctx.rows:
        if not any(k in row.description.lower() for k in keywords):
            continue
        others = [r for r in ctx.rows if r is not row]
        others_pct = pct_of(
            sum((r.total_completed_stored for r in others), ZERO),
            sum((r.scheduled_value for r in others), ZERO),
        )
        line_pct = pct_of(row.total_completed_stored, row.scheduled_value)
        gap = line_pct - others_pct
        if gap <= threshold:
            continue
        at_pace = apply_pct(row.scheduled_value, others_pct)
        yield finding(
            severity=Severity.WARNING,
            item_no=row.item_no,
            column="pct",
            expected=others_pct,
            actual=line_pct,
            message=f"{_line(row)}: billed {fmt_pct(line_pct)} complete while the rest of the "
            f"work is {fmt_pct(others_pct)} complete ({gap:.2f} points ahead; threshold "
            f"{threshold}). At the pace of the work, this line would be about "
            f"{fmt_money(at_pace)}, not {fmt_money(row.total_completed_stored)}. "
            "General conditions should track the schedule; confirm with the PM before "
            "certifying.",
        )


@rule("R10", "SOV structure")
def check_sov_structure(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
    seen: set[str] = set()
    for row in ctx.review.submitted:
        if row.item_no in seen:
            yield finding(
                severity=Severity.ERROR,
                item_no=row.item_no,
                column="A",
                actual=row.total_completed_stored,
                dollar_impact=row.total_completed_stored,
                message=f"{_line(row)}: item number appears more than once on the G703. The "
                f"duplicate row bills {fmt_money(row.total_completed_stored)} and is counted "
                f"once.",
            )
        seen.add(row.item_no)

    for row in ctx.rows:
        if row.item_no not in ctx.contract and not is_change_order_item(row.item_no):
            yield finding(
                severity=Severity.ERROR,
                item_no=row.item_no,
                column="A",
                actual=row.total_completed_stored,
                dollar_impact=row.total_completed_stored,
                message=f"{_line(row)}: not on the approved schedule of values. New scope "
                "needs an approved change order; SOV changes need GC approval.",
            )

    for item_no, line in ctx.contract.items():
        prior = ctx.prior_work.get(item_no, ZERO)
        if item_no in seen or (line.is_change_order_line and not prior):
            continue
        message = (
            f"Line {item_no} ({line.description}) is missing from the G703. Every SOV line is "
            "carried on every application, even at zero."
        )
        if prior:
            message += f" {fmt_money(prior)} was certified on this line previously."
        yield finding(severity=Severity.ERROR, item_no=item_no, column="A", message=message)
