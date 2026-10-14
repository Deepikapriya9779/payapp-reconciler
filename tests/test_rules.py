"""Unit tests per rule: each rule passes a clean application and catches its error."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from conftest import (
    CURRENT,
    PERIOD_1,
    PRIOR,
    SUB,
    findings,
    replace_row,
    review,
    row,
    with_g702,
)

from payapp_reconciler.loaders import ReviewInput
from payapp_reconciler.models import ReviewConfig, Severity
from payapp_reconciler.rules import RULES, run_rules

D = Decimal


def test_rule_registry_has_ten_rules_in_order() -> None:
    assert [r.rule_id for r in RULES] == [f"R{n:02d}" for n in range(1, 11)]


def test_clean_application_has_no_findings(clean: ReviewInput) -> None:
    assert run_rules(clean) == []


# ---------------------------------------------------------------- R01 row math


def test_r01_g_not_equal_d_plus_e_plus_f() -> None:
    bad = row("02", "Ductwork", "65000", "12000", "18000").model_copy(
        update={
            "total_completed_stored": D("31000"),
            "balance_to_finish": D("34000"),
            "pct_complete": D("47.69"),
            "retainage": D("3100"),
        }
    )
    [f] = findings(review(replace_row(CURRENT, "02", bad)), "R01")
    assert (f.item_no, f.column, f.severity) == ("02", "G", Severity.ERROR)
    assert f.expected == D("30000") and f.dollar_impact == D("1000")


def test_r01_balance_to_finish_and_pct() -> None:
    bad = row("02", "Ductwork", "65000", "12000", "18000").model_copy(
        update={"balance_to_finish": D("30000"), "pct_complete": D("50")}
    )
    result = findings(review(replace_row(CURRENT, "02", bad)), "R01")
    assert [(f.column, f.severity) for f in result] == [
        ("H", Severity.ERROR),
        ("pct", Severity.INFO),
    ]
    assert all(f.dollar_impact == 0 for f in result)


def test_r01_one_cent_rounding_is_tolerated() -> None:
    off = row("02", "Ductwork", "65000", "12000", "18000").model_copy(
        update={"total_completed_stored": D("30000.01"), "balance_to_finish": D("34999.99")}
    )
    assert findings(review(replace_row(CURRENT, "02", off)), "R01") == []


# ---------------------------------------------------------------- R02 scheduled value


def test_r02_scheduled_value_must_include_approved_co() -> None:
    stale = row("02", "Ductwork", "60000", "12000", "18000")  # forgot approved CO-01
    [f] = findings(review(replace_row(CURRENT, "02", stale)), "R02")
    assert (f.item_no, f.column, f.expected, f.actual) == ("02", "C", D("65000"), D("60000"))


def test_r02_co_approved_after_period_is_not_yet_in_scheduled_value() -> None:
    # On application 1 (period ending Jan 31) CO-01 (approved Jan 15) counts...
    app1 = [row("01", "General Conditions", "10000", "0", "2000"), *CURRENT[1:]]
    assert findings(review(app1, prior=None, app_no=1, period=PERIOD_1), "R02") == []
    # ...but against a period ending Jan 10 it does not.
    early = review(app1, prior=None, app_no=1, period=PERIOD_1.replace(day=10))
    [f] = findings(early, "R02")
    assert f.expected == D("60000")
    assert "CO-01 was approved 2026-01-15, after this period" in f.message


def test_r02_names_pending_co_that_explains_difference() -> None:
    pending = dataclasses.replace(
        review(),
        change_orders=[
            *review().change_orders,
            review().change_orders[1].model_copy(update={"co_no": "CO-04", "sov_line_no": "03"}),
        ],
    )
    inflated = replace_row(CURRENT, "03", row("03", "Equipment", "34000", "0", "0"))
    [f] = findings(dataclasses.replace(pending, submitted=inflated), "R02")
    assert "CO-04 is pending" in f.message


# ---------------------------------------------------------------- R03 from previous


def test_r03_from_previous_must_match_prior_certificate() -> None:
    carried = row("02", "Ductwork", "65000", "15000", "18000")  # carried its own 15,000
    [f] = findings(review(replace_row(CURRENT, "02", carried)), "R03")
    assert (f.column, f.expected, f.actual, f.dollar_impact) == (
        "D",
        D("12000"),
        D("15000"),
        D("3000"),
    )
    assert "certificate for application 1" in f.message


def test_r03_application_one_must_start_at_zero() -> None:
    app1 = [row("01", "General Conditions", "10000", "500", "1500"), *CURRENT[1:]]
    result = findings(review(app1, prior=None, app_no=1), "R03")
    assert {f.item_no for f in result} == {"01", "02"}
    assert "application 1 should show $0.00" in result[0].message


def test_r03_explains_stored_materials_carried_into_d() -> None:
    prior = replace_row(PRIOR, "03", row("03", "Equipment", "30000", "0", "0", f="8000"))
    current = replace_row(CURRENT, "03", row("03", "Equipment", "30000", "8000", "0"))
    [f] = findings(review(current, prior=prior), "R03")
    assert "they are billed in E (this period), not D" in f.message


# ---------------------------------------------------------------- R04 over-billing


def test_r04_over_billing_against_trusted_scheduled_value() -> None:
    over = row("03", "Equipment", "30000", "0", "31500")
    [f] = findings(review(replace_row(CURRENT, "03", over)), "R04")
    assert (f.item_no, f.dollar_impact) == ("03", D("1500"))
    assert "(105%)" in f.message


def test_r04_uses_trusted_c_even_if_sub_inflates_c() -> None:
    sneaky = row("03", "Equipment", "32000", "0", "31500")
    [f] = findings(review(replace_row(CURRENT, "03", sneaky)), "R04")
    assert f.expected == D("30000")


# ---------------------------------------------------------------- R05 unapproved COs


def test_r05_billing_on_pending_co() -> None:
    rows = [*CURRENT, row("CO-02", "Added VAV zone", "4000", "0", "2000")]
    [f] = findings(review(rows), "R05")
    assert (f.item_no, f.severity, f.dollar_impact) == ("CO-02", Severity.ERROR, D("2000"))
    assert "CO-02 is pending" in f.message


def test_r05_rejected_and_unknown_cos() -> None:
    rows = [
        *CURRENT,
        row("CO-03", "Upsized RTU", "2000", "0", "2000"),
        row("CO-09", "Owner extras", "1500", "0", "1500"),
    ]
    result = findings(review(rows), "R05")
    assert [f.item_no for f in result] == ["CO-03", "CO-09"]
    assert "CO-03 is rejected" in result[0].message
    assert "not in the change order log" in result[1].message


def test_r05_listed_but_unbilled_pending_co_is_a_warning() -> None:
    rows = [*CURRENT, row("CO-02", "Added VAV zone", "4000", "0", "0")]
    [f] = findings(review(rows), "R05")
    assert f.severity is Severity.WARNING and f.dollar_impact == 0


# ---------------------------------------------------------------- R06 retainage


def _past_threshold(rate: str) -> ReviewInput:
    """Application 2 at 60% complete (63,000 of 105,000), every line at `rate`."""
    rows = [
        row("01", "General Conditions", "10000", "2000", "4000", rate=rate),
        row("02", "Ductwork", "65000", "12000", "27000", rate=rate),
        row("03", "Equipment", "30000", "0", "18000", rate=rate),
    ]
    return review(rows)


def test_r06_reduced_rate_applied_correctly() -> None:
    assert findings(_past_threshold("5"), "R06") == []


def test_r06_missed_retainage_reduction_is_one_app_level_finding() -> None:
    [f] = findings(_past_threshold("10"), "R06")
    assert (f.item_no, f.column) == (None, "I")
    assert f.expected == D("3150.00") and f.actual == D("6300.00")
    assert f.dollar_impact == D("-3150.00")  # the sub is under-billing
    assert "held at 10%" in f.message and "60% complete" in f.message


def test_r06_early_reduction() -> None:
    rows = [row(r.item_no, r.description, str(r.scheduled_value), str(r.from_previous),
                str(r.this_period), rate="5") for r in CURRENT]  # fmt: skip
    [f] = findings(review(rows), "R06")
    assert "reduced to 5% early" in f.message
    assert f.dollar_impact > 0


def test_r06_single_line_slip_is_reported_per_line() -> None:
    slip = row("02", "Ductwork", "65000", "12000", "18000").model_copy(
        update={"retainage": D("2900")}
    )
    [f] = findings(review(replace_row(CURRENT, "02", slip)), "R06")
    assert (f.item_no, f.expected, f.dollar_impact) == ("02", D("3000.00"), D("100.00"))


def test_r06_threshold_uses_certifiable_not_claimed_progress() -> None:
    # Carrying an inflated D pushes claimed progress to 51.4%; certifiable progress is 34.3%.
    rows = [
        row("01", "General Conditions", "10000", "2000", "2000", rate="5"),
        row("02", "Ductwork", "65000", "30000", "18000", rate="5"),
        row("03", "Equipment", "30000", "0", "2000", rate="5"),
    ]
    result = findings(review(rows), "R06")
    assert result and "early" in result[0].message


def test_r06_flat_retainage_subcontract() -> None:
    flat = SUB.model_copy(
        update={"retainage_reduced_pct": None, "retainage_reduction_threshold_pct": None}
    )
    rows = [row("01", "General Conditions", "10000", "2000", "2000", rate="9"), *CURRENT[1:]]
    [f] = findings(review(rows, subcontract=flat), "R06")
    assert f.item_no == "01"


# ---------------------------------------------------------------- R07 stored materials


def test_r07_stored_materials_need_backup() -> None:
    stored = row("03", "Equipment", "30000", "0", "0", f="12500")
    [f] = findings(review(replace_row(CURRENT, "03", stored)), "R07")
    assert (f.severity, f.column, f.actual) == (Severity.WARNING, "F", D("12500"))
    assert "bill of lading" in f.message


# ---------------------------------------------------------------- R08 G702 tie-out


def test_r08_line_4_keying_error_is_reported_once(clean: ReviewInput) -> None:
    g = clean.g702
    bad = with_g702(
        clean,
        total_completed_and_stored=g.total_completed_and_stored + 9000,
        total_earned_less_retainage=g.total_earned_less_retainage + 9000,
        current_payment_due=g.current_payment_due + 9000,
        balance_to_finish_including_retainage=g.balance_to_finish_including_retainage - 9000,
    )
    [f] = findings(bad, "R08")
    assert (f.column, f.dollar_impact) == ("L4", D("9000"))


def test_r08_each_line_checked(clean: ReviewInput) -> None:
    g = clean.g702
    bad = with_g702(
        clean,
        original_contract_sum=g.original_contract_sum + 1,
        net_change_by_change_orders=g.net_change_by_change_orders + 4000,  # pending CO-02
        retainage=g.retainage - 100,
        less_previous_certificates=g.less_previous_certificates - 500,
    )
    result = {f.column: f for f in findings(bad, "R08")}
    # Lines 3, 6 and 8 no longer tie to the sub's own upstream lines; line 9 still does.
    assert set(result) == {"L1", "L2", "L3", "L5", "L6", "L7", "L8"}
    assert result["L5"].dollar_impact == D("100")  # less retainage = more money claimed
    assert result["L7"].dollar_impact == D("500")
    assert result["L1"].dollar_impact == 0


def test_r08_application_one_has_no_previous_certificates() -> None:
    app1 = [row("01", "General Conditions", "10000", "0", "2000"), *CURRENT[1:]]
    r = review(app1, prior=None, app_no=1, period=PERIOD_1)
    bad = with_g702(r, less_previous_certificates=D("1000"))
    assert "nothing on application 1" in findings(bad, "R08")[0].message


# ---------------------------------------------------------------- R09 front-loading


def test_r09_general_conditions_far_ahead_of_the_work() -> None:
    ahead = row("01", "General Conditions", "10000", "2000", "6000")  # 80% vs ~32%
    [f] = findings(review(replace_row(CURRENT, "01", ahead)), "R09")
    assert (f.item_no, f.severity, f.column) == ("01", Severity.WARNING, "pct")
    assert f.actual == D("80.00") and f.expected == D("31.58")


def test_r09_threshold_is_configurable_and_mobilization_is_ignored() -> None:
    ahead = row("01", "General Conditions", "10000", "2000", "6000")
    r = review(replace_row(CURRENT, "01", ahead))
    assert [f for f in run_rules(r, ReviewConfig(front_load_threshold_pts=D("60")))] == []
    mob = replace_row(CURRENT, "01", row("01", "Mobilization", "10000", "2000", "8000"))
    assert findings(review(mob), "R09") == []


# ---------------------------------------------------------------- R10 SOV structure


def test_r10_duplicate_line_number() -> None:
    rows = [*CURRENT, CURRENT[1]]
    [f] = findings(review(rows), "R10")
    assert (f.item_no, f.dollar_impact) == ("02", D("30000"))


def test_r10_missing_and_extra_lines() -> None:
    rows = [CURRENT[0], CURRENT[2], row("04", "Temporary heat", "0", "0", "1200")]
    result = findings(review(rows), "R10")
    by_item = {f.item_no: f for f in result}
    assert set(by_item) == {"02", "04"}
    assert by_item["04"].dollar_impact == D("1200")
    assert "$12,000.00 was certified on this line previously" in by_item["02"].message
