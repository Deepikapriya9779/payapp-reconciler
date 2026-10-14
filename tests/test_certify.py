"""Certifiable amount: rebuilt from trusted inputs, checked against the sample's certificates."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from conftest import CURRENT, replace_row, review, row

from payapp_reconciler.loaders import load_review, read_csv
from payapp_reconciler.models import G703Row
from payapp_reconciler.review import review_application, review_input, write_findings_json

D = Decimal
SAMPLE = Path(__file__).parents[1] / "data" / "sample"
KEY = json.loads((SAMPLE / "expected_findings.json").read_text(encoding="utf-8"))
CERTIFIED_APPS = [a for a in KEY["applications"] if a["app_no"] <= 4]


def test_clean_application_has_zero_variance() -> None:
    result = review_input(review())
    assert result.variance == 0
    assert [line.certifiable for line in result.g702] == [line.claimed for line in result.g702]


def test_variance_is_claimed_minus_certifiable() -> None:
    # Sub carries 3,000 too much in D: G is 3,000 high, payment 2,700 high at 10% retainage.
    rows = replace_row(CURRENT, "02", row("02", "Ductwork", "65000", "15000", "18000"))
    result = review_input(review(rows))
    assert result.g702_line(4).variance == D("3000")
    assert result.variance == D("2700.00")


@pytest.mark.parametrize("app", CERTIFIED_APPS, ids=lambda a: f"{a['sub_id']}-app{a['app_no']}")
def test_certifiable_matches_what_the_gc_certified(app: dict[str, Any]) -> None:
    """For apps 1-4, the GC's certificate exists. Certifiable minus certified must be exactly
    the PM's field cuts listed in the answer key (the tool can't see the jobsite)."""
    sub, n = app["sub_id"], app["app_no"]
    _, result = review_application(SAMPLE, sub, n)
    certified = {r.item_no: r for r in read_csv(SAMPLE / f"certified/{sub}_app_{n}.csv", G703Row)}
    cuts = {c["item_no"]: D(c["amount"]) for c in app["field_adjustments"]}

    for line in result.certifiable_lines:
        cert = certified.get(line.item_no)
        cert_g = cert.total_completed_stored if cert else D(0)
        assert line.total_completed_stored - cert_g == cuts.get(line.item_no, 0), line.item_no
        if line.item_no not in cuts and cert:
            assert line.retainage == cert.retainage, line.item_no
    if not cuts:
        cert_l6 = sum((r.total_completed_stored - r.retainage for r in certified.values()), D(0))
        assert result.g702_line(6).certifiable == cert_l6


def test_sample_variances() -> None:
    """Dollar effect of each planted error on the current payment due."""
    variances = {
        (a["sub_id"], a["app_no"]): review_application(SAMPLE, a["sub_id"], a["app_no"])[1].variance
        for a in KEY["applications"]
    }
    assert variances[("mech", 2)] == D("9000.00")  # $10,000 G formula error, net of 10%
    assert variances[("mech", 4)] == D("8787.50")  # $9,250 on pending CO, net of 5%
    assert variances[("mech", 5)] == D("9310.00")  # $9,800 over-billed, net of 5%
    assert variances[("elec", 2)] == D("27000.00")  # $30,000 carried into D, net of 10%
    assert variances[("elec", 3)] == D("-54720.00")  # retainage over-held: GC owes more
    assert variances[("dryw", 3)] == D("9000.00")  # G702 line 4 keyed high
    assert variances[("dryw", 5)] == D("44352.00")  # duplicate row, net of 10%
    clean = [a for a in KEY["applications"] if a["clean"] and not a["field_adjustments"]]
    assert all(variances[(a["sub_id"], a["app_no"])] == 0 for a in clean)


def test_findings_json_round_trips(tmp_path: Path) -> None:
    _, result = review_application(SAMPLE, "mech", 4)
    path = tmp_path / "out" / "findings.json"
    write_findings_json(result, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["project"] == "Desert Ridge Medical Office Building"
    assert data["variance"] == "8787.50"  # Decimals serialize as strings, never floats
    assert data["g702"][7]["variance"] == "8787.50"
    assert [f["rule_id"] for f in data["findings"]] == ["R05", "R08"]
    assert load_review(SAMPLE, "mech", 4).project.general_contractor == "Arroyo Peak Builders"
