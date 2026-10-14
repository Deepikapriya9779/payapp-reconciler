"""Generate the fictional Desert Ridge Medical Office Building sample project.

Every company, person and number here is invented. The script is deterministic: running it twice
produces identical files, and a test checks that the committed data/sample matches its output.

How it works:
1. Each subcontract has an SOV and a cumulative % complete per line for five monthly
   applications. That is the "true" progress, and the GC certifies it.
2. Each submitted application starts from the truth, then a `Plan` plants specific errors
   (or a claim the PM cuts in the field).
3. The answer key lists every planted error and the findings it should produce.

Usage: python scripts/generate_sample.py [--out data/sample]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from payapp_reconciler.money import ZERO, apply_pct, pct_of, to_cents

D = Decimal
PROJECT = "Desert Ridge Medical Office Building"
GC = "Arroyo Peak Builders"  # fictional
PERIODS = [
    dt.date(2026, 1, 31),
    dt.date(2026, 2, 28),
    dt.date(2026, 3, 31),
    dt.date(2026, 4, 30),
    dt.date(2026, 5, 31),
]
APPS = range(1, len(PERIODS) + 1)


# ---------------------------------------------------------------- contract data


@dataclass(frozen=True)
class Line:
    item_no: str
    description: str
    value: Decimal
    progress: tuple[int, ...]  # cumulative % of work completed (excl. stored) after each app
    stored: dict[int, int] = field(default_factory=dict)  # app -> % of value stored on site


@dataclass(frozen=True)
class CO:
    co_no: str
    description: str
    amount: Decimal
    status: str
    line: str  # an existing SOV line, or its own "CO-xx" G703 line
    approved: dt.date | None = None
    progress: tuple[int, ...] = (0, 0, 0, 0, 0)  # only used when the CO has its own line

    def approved_by(self, as_of: dt.date) -> bool:
        return self.status == "approved" and (self.approved is None or self.approved <= as_of)


@dataclass(frozen=True)
class Sub:
    sub_id: str
    name: str
    trade: str
    retainage: Decimal
    reduced: Decimal | None
    threshold: Decimal | None
    lines: list[Line]
    cos: list[CO]

    @property
    def contract_sum(self) -> Decimal:
        return sum((line.value for line in self.lines), ZERO)


SUBS = [
    Sub(
        "mech", "Sonoran Air Mechanical", "Mechanical (HVAC & Plumbing)", D(10), D(5), D(50),
        [
            Line("01", "General Conditions / Supervision", D(147000), (10, 20, 30, 40, 50)),
            Line("02", "Mobilization", D(49000), (100, 100, 100, 100, 100)),
            Line("03", "Submittals & Coordination Drawings", D(36750), (60, 100, 100, 100, 100)),
            Line("04", "Underground Plumbing", D(171500), (50, 100, 100, 100, 100)),
            Line("05", "Domestic Water Piping", D(245000), (0, 20, 45, 70, 100)),
            Line("06", "Sanitary Waste & Vent", D(196000), (10, 35, 60, 80, 90)),
            Line("07", "Ductwork - Fabricate & Install", D(539000), (0, 15, 40, 65, 85)),
            Line("08", "Rooftop Units & HVAC Equipment", D(441000), (0, 0, 0, 60, 90), {3: 50}),
            Line("09", "VAV Boxes & Building Controls", D(294000), (0, 0, 10, 35, 60)),
            Line("10", "Duct & Pipe Insulation", D(122500), (0, 0, 15, 40, 65)),
            Line("11", "Test & Balance", D(73500), (0, 0, 0, 0, 10)),
            Line("12", "Startup, Commissioning & Closeout", D(134750), (0, 0, 0, 0, 0)),
        ],
        [
            CO("CO-01", "Added exhaust ductwork at imaging suite", D(18400), "approved", "07",
               dt.date(2026, 2, 15)),
            CO("CO-02", "Medical gas piping at exam rooms", D(42600), "approved", "CO-02",
               dt.date(2026, 3, 10), (0, 0, 0, 40, 100)),
            CO("CO-03", "Added VAV zone, 2nd floor east", D(18500), "pending", "CO-03"),
        ],
    ),
    Sub(
        "elec", "Copperline Electric", "Electrical", D(10), D(5), D(50),
        [
            Line("01", "General Conditions / Project Management", D(118800), (15, 30, 45, 60, 75)),
            Line("02", "Mobilization", D(39600), (100, 100, 100, 100, 100)),
            Line("03", "Underground Conduit & Grounding", D(158400), (70, 100, 100, 100, 100)),
            Line("04", "Service Entrance & Switchgear", D(316800), (0, 10, 60, 90, 100)),
            Line("05", "Branch Rough-In", D(396000), (20, 45, 70, 90, 100)),
            Line("06", "Feeders & Panelboards", D(237600), (0, 25, 60, 85, 100)),
            Line("07", "Lighting Fixtures & Controls", D(277200), (0, 0, 30, 60, 90)),
            Line("08", "Fire Alarm System", D(138600), (0, 20, 50, 70, 90)),
            Line("09", "Low Voltage Rough-In", D(99000), (0, 25, 60, 85, 100)),
            Line("10", "Devices & Trim-Out", D(118800), (0, 0, 10, 40, 75)),
            Line("11", "Testing, Closeout & O&M Manuals", D(79200), (0, 0, 0, 0, 10)),
        ],
        [
            CO("CO-E01", "Upgraded LED fixtures at main lobby", D(31200), "approved", "07",
               dt.date(2026, 2, 20)),
            CO("CO-E02", "Temporary power extension to staging area", D(12800), "rejected",
               "CO-E02"),
        ],
    ),
    Sub(
        "dryw", "Mesa Wall Systems", "Framing & Drywall", D(10), None, None,
        [
            Line("01", "General Conditions / Supervision", D(67200), (10, 20, 35, 50, 65)),
            Line("02", "Mobilization", D(22400), (100, 100, 100, 100, 100)),
            Line("03", "Metal Stud Framing", D(302400), (15, 40, 70, 90, 100)),
            Line("04", "Exterior Sheathing & Weather Barrier", D(134400), (10, 35, 70, 100, 100)),
            Line("05", "Gypsum Board - Hang", D(246400), (0, 10, 35, 60, 85)),
            Line("06", "Tape & Finish (Level 5 at Lobby)", D(179200), (0, 0, 15, 40, 70)),
            Line("07", "Acoustical Ceilings", D(123200), (0, 0, 0, 15, 40)),
            Line("08", "Shaft Wall & Fire-Rated Assemblies", D(44800), (0, 20, 50, 80, 100)),
        ],
        [
            CO("CO-D01", "Added soffit framing at entry canopy", D(16800), "approved", "CO-D01",
               dt.date(2026, 3, 5), (0, 0, 50, 100, 100)),
            CO("CO-D02", "Level 5 finish at corridor walls", D(22400), "pending", "CO-D02"),
        ],
    ),
]  # fmt: skip


# ---------------------------------------------------------------- planted errors


@dataclass(frozen=True)
class Expect:
    rule_id: str
    severity: str
    item_no: str | None
    column: str


@dataclass(frozen=True)
class Plan:
    """What goes wrong on one submitted application."""

    error: str = ""
    description: str = ""
    expected: tuple[Expect, ...] = ()
    field_cut: tuple[str, Decimal, str] | None = None  # item, E claimed above truth, PM's reason
    from_previous_delta: tuple[str, Decimal] | None = None  # item, amount added to D
    this_period_delta: tuple[str, Decimal] | None = None  # item, amount added to E
    g_formula_error: tuple[str, Decimal] | None = None  # item, amount G is off from D+E+F
    extra_row: tuple[str, str, Decimal] | None = None  # item, description, amount billed
    g702_net_change_delta: Decimal = ZERO
    g702_line4_delta: Decimal = ZERO
    retainage_rate: Decimal | None = None  # rate the sub used instead of the correct one
    duplicate_item: str | None = None


PLANS: dict[tuple[str, int], Plan] = {
    ("mech", 2): Plan(
        "math error",
        "Spreadsheet formula on sanitary waste & vent adds $10,000 to G that isn't in D+E+F.",
        (Expect("R01", "error", "06", "G"),),
        g_formula_error=("06", D(10000)),
    ),
    ("mech", 3): Plan(
        "stored materials",
        "Rooftop units delivered and stored on site: $220,500 billed in column F.",
        (Expect("R07", "warning", "08", "F"),),
    ),
    ("mech", 4): Plan(
        "billing on a pending CO",
        "Bills 50% of pending CO-03 on its own line and adds it to G702 line 2.",
        (Expect("R05", "error", "CO-03", "G"), Expect("R08", "error", None, "L2")),
        extra_row=("CO-03", "Added VAV zone, 2nd floor east", D(9250)),
        g702_net_change_delta=D(18500),
    ),
    ("mech", 5): Plan(
        "over-billed line",
        "Domestic water piping billed at 104% of its scheduled value with no change order.",
        (Expect("R04", "error", "05", "G"),),
        this_period_delta=("05", D(9800)),
    ),
    ("elec", 1): Plan(
        field_cut=("05", D(30000), "PM field verification: branch rough-in is 20%, not 27.6%"),
    ),
    ("elec", 2): Plan(
        "wrong from-previous",
        "Carries its own application 1 billing on branch rough-in into D, not the GC's "
        "certified amount (the PM cut $30,000 on application 1).",
        (Expect("R03", "error", "05", "D"),),
        from_previous_delta=("05", D(30000)),
    ),
    ("elec", 3): Plan(
        "missed retainage reduction",
        "Work passes 50% complete but retainage is still held at 10% instead of 5%.",
        (Expect("R06", "error", None, "I"),),
        retainage_rate=D(10),
    ),
    ("elec", 5): Plan(
        "billing on a CO not in the log",
        "Bills CO-E07 (EV charger circuits) which isn't in the change order log, and adds it "
        "to G702 line 2.",
        (Expect("R05", "error", "CO-E07", "G"), Expect("R08", "error", None, "L2")),
        extra_row=("CO-E07", "Added EV charger circuits", D(14750)),
        g702_net_change_delta=D(14750),
    ),
    ("dryw", 2): Plan(
        "front-loaded general conditions",
        "General conditions billed at 65% while the rest of the work is about 21% complete.",
        (Expect("R09", "warning", "01", "pct"),),
        field_cut=("01", D(30240), "PM holds general conditions to 20%, in line with the work"),
    ),
    ("dryw", 3): Plan(
        "G702 doesn't tie",
        "G702 line 4 keyed $9,000 higher than the G703 total; lines 6, 8 and 9 follow it.",
        (Expect("R08", "error", None, "L4"),),
        g702_line4_delta=D(9000),
    ),
    ("dryw", 5): Plan(
        "duplicate line",
        "Acoustical ceilings row pasted twice on the G703.",
        (Expect("R10", "error", "07", "A"),),
        duplicate_item="07",
    ),
}


# ---------------------------------------------------------------- truth


@dataclass
class Row:
    item_no: str
    description: str
    c: Decimal
    d: Decimal
    e: Decimal
    f: Decimal
    g: Decimal = ZERO
    pct: Decimal = ZERO
    h: Decimal = ZERO
    i: Decimal = ZERO

    def finish(self, rate: Decimal, g_error: Decimal = ZERO) -> Row:
        """Fill in G, %, H and I the way the sub's spreadsheet does."""
        self.g = self.d + self.e + self.f + g_error
        self.pct = pct_of(self.g, self.c)
        self.h = self.c - self.g
        self.i = apply_pct(self.g, rate)
        return self


def net_change(sub: Sub, as_of: dt.date) -> Decimal:
    return sum((co.amount for co in sub.cos if co.approved_by(as_of)), ZERO)


def work_completed(sub: Sub, app: int) -> dict[str, tuple[str, Decimal, Decimal, Decimal]]:
    """item -> (description, C, cumulative work completed, stored) as of `app` (0 = nothing)."""
    if app == 0:
        return {}
    as_of = PERIODS[app - 1]
    out = {}
    for line in sub.lines:
        c = line.value + sum(
            (co.amount for co in sub.cos if co.line == line.item_no and co.approved_by(as_of)),
            ZERO,
        )
        done = to_cents(c * line.progress[app - 1] / 100)
        stored = to_cents(c * line.stored.get(app, 0) / 100)
        out[line.item_no] = (line.description, c, done, stored)
    for co in sub.cos:
        if co.line.startswith("CO") and co.approved_by(as_of):
            done = to_cents(co.amount * co.progress[app - 1] / 100)
            out[co.line] = (co.description, co.amount, done, ZERO)
    return out


def rate_for(sub: Sub, total_g: Decimal, contract: Decimal) -> Decimal:
    reached = sub.threshold is not None and pct_of(total_g, contract) >= sub.threshold
    return sub.reduced if reached and sub.reduced is not None else sub.retainage


def true_progress(sub: Sub, app: int) -> tuple[list[Row], Decimal]:
    """Unfinished rows (C, D, E, F) for the true progress, and the retainage rate that applies."""
    now, before = work_completed(sub, app), work_completed(sub, app - 1)
    rows = []
    for item, (desc, c, done, stored) in now.items():
        prev = before.get(item, ("", ZERO, ZERO, ZERO))[2]
        rows.append(Row(item, desc, c, prev, done - prev, stored))
    contract = sub.contract_sum + net_change(sub, PERIODS[app - 1])
    rate = rate_for(sub, sum((r.d + r.e + r.f for r in rows), ZERO), contract)
    return rows, rate


def certified_rows(sub: Sub, app: int) -> list[Row]:
    """What the GC certified: the true progress."""
    rows, rate = true_progress(sub, app)
    return [r.finish(rate) for r in rows]


def line_6(rows: list[Row]) -> Decimal:
    return sum((r.g - r.i for r in rows), ZERO)


# ---------------------------------------------------------------- submitted


def submitted(sub: Sub, app: int) -> tuple[list[Row], dict[str, object]]:
    plan = PLANS.get((sub.sub_id, app), Plan())
    rows, true_rate = true_progress(sub, app)
    by_item = {r.item_no: r for r in rows}

    for adjustment in (plan.field_cut, plan.this_period_delta):
        if adjustment:
            by_item[adjustment[0]].e += adjustment[1]
    if plan.from_previous_delta:
        by_item[plan.from_previous_delta[0]].d += plan.from_previous_delta[1]
    if plan.extra_row:
        item, desc, billed = plan.extra_row
        amount = next((co.amount for co in sub.cos if co.line == item), billed)
        rows.append(Row(item, desc, amount, ZERO, billed, ZERO))

    rate = plan.retainage_rate or true_rate
    g_item, g_error = plan.g_formula_error or ("", ZERO)
    for r in rows:
        r.finish(rate, g_error if r.item_no == g_item else ZERO)
    if plan.duplicate_item:
        dup = by_item[plan.duplicate_item]
        rows.insert(rows.index(dup) + 1, Row(**vars(dup)))

    as_of = PERIODS[app - 1]
    l1 = sub.contract_sum
    l2 = net_change(sub, as_of) + plan.g702_net_change_delta
    l4 = sum((r.g for r in rows), ZERO) + plan.g702_line4_delta
    l5 = sum((r.i for r in rows), ZERO)
    l7 = line_6(certified_rows(sub, app - 1)) if app > 1 else ZERO
    g702 = {
        "sub_id": sub.sub_id,
        "application_no": app,
        "period_to": as_of.isoformat(),
        "original_contract_sum": l1,
        "net_change_by_change_orders": l2,
        "contract_sum_to_date": l1 + l2,
        "total_completed_and_stored": l4,
        "retainage": l5,
        "total_earned_less_retainage": l4 - l5,
        "less_previous_certificates": l7,
        "current_payment_due": l4 - l5 - l7,
        "balance_to_finish_including_retainage": l1 + l2 - (l4 - l5),
    }
    return rows, g702


# ---------------------------------------------------------------- writing


G703_HEADER = [
    "item_no", "description", "scheduled_value", "from_previous", "this_period",
    "materials_stored", "total_completed_stored", "pct_complete", "balance_to_finish",
    "retainage",
]  # fmt: skip


def money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, default=lambda v: money(v) if isinstance(v, D) else v)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def g703_rows(rows: list[Row]) -> list[list[str]]:
    return [
        [r.item_no, r.description, money(r.c), money(r.d), money(r.e), money(r.f), money(r.g),
         f"{r.pct:.2f}", money(r.h), money(r.i)]
        for r in rows
    ]  # fmt: skip


def answer_key() -> dict[str, object]:
    applications = []
    for sub in SUBS:
        for app in APPS:
            plan = PLANS.get((sub.sub_id, app), Plan())
            planted = []
            if plan.error:
                planted.append(
                    {
                        "error": plan.error,
                        "description": plan.description,
                        "expected_findings": [vars(e) for e in plan.expected],
                    }
                )
            cuts = []
            if plan.field_cut:
                item, amount, reason = plan.field_cut
                cuts.append({"item_no": item, "amount": money(amount), "reason": reason})
            applications.append(
                {
                    "sub_id": sub.sub_id,
                    "app_no": app,
                    "period_to": PERIODS[app - 1].isoformat(),
                    "clean": not plan.expected,
                    "planted": planted,
                    "field_adjustments": cuts,
                }
            )
    return {
        "project": PROJECT,
        "general_contractor": GC,
        "note": "Fictional data. 'field_adjustments' are PM cuts made in the field: the tool "
        "accepts claimed progress, so certifiable minus certified equals these amounts.",
        "applications": applications,
    }


def generate(out: Path) -> None:
    write_json(out / "project.json", {"name": PROJECT, "general_contractor": GC})
    write_csv(
        out / "subcontracts.csv",
        ["sub_id", "sub_name", "trade", "original_contract_sum", "retainage_pct",
         "retainage_reduced_pct", "retainage_reduction_threshold_pct"],
        [[s.sub_id, s.name, s.trade, money(s.contract_sum), str(s.retainage),
          "" if s.reduced is None else str(s.reduced),
          "" if s.threshold is None else str(s.threshold)] for s in SUBS],
    )  # fmt: skip
    write_csv(
        out / "sov.csv",
        ["sub_id", "line_no", "description", "scheduled_value"],
        [[s.sub_id, ln.item_no, ln.description, money(ln.value)] for s in SUBS for ln in s.lines],
    )
    write_csv(
        out / "change_orders.csv",
        ["co_no", "sub_id", "description", "amount", "status", "sov_line_no", "approved_date"],
        [[co.co_no, s.sub_id, co.description, money(co.amount), co.status, co.line,
          co.approved.isoformat() if co.approved else ""] for s in SUBS for co in s.cos],
    )  # fmt: skip
    for sub in SUBS:
        for app in APPS:
            if app < len(PERIODS):  # the latest application hasn't been certified yet
                path = out / "certified" / f"{sub.sub_id}_app_{app}.csv"
                write_csv(path, G703_HEADER, g703_rows(certified_rows(sub, app)))
            rows, g702 = submitted(sub, app)
            write_csv(
                out / "submitted" / f"{sub.sub_id}_app_{app}.csv", G703_HEADER, g703_rows(rows)
            )
            write_json(out / "submitted" / f"{sub.sub_id}_app_{app}_g702.json", g702)
    write_json(out / "expected_findings.json", answer_key())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).parents[1] / "data" / "sample")
    generate(parser.parse_args().out)


if __name__ == "__main__":
    main()
