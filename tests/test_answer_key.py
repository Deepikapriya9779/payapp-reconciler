"""The synthetic project's answer key: every planted error is caught, and nothing else is."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from payapp_reconciler.loaders import load_review
from payapp_reconciler.models import Finding
from payapp_reconciler.rules import run_rules

SAMPLE = Path(__file__).parents[1] / "data" / "sample"
KEY = json.loads((SAMPLE / "expected_findings.json").read_text(encoding="utf-8"))
APPS = KEY["applications"]


def _id(app: dict[str, Any]) -> str:
    return f"{app['sub_id']}-app{app['app_no']}"


def _signature(f: Finding) -> tuple[str, str, str | None, str | None]:
    return (f.rule_id, f.severity.value, f.item_no, f.column)


def _expected(app: dict[str, Any]) -> set[tuple[str, str, str | None, str | None]]:
    return {
        (e["rule_id"], e["severity"], e["item_no"], e["column"])
        for planted in app["planted"]
        for e in planted["expected_findings"]
    }


@pytest.mark.parametrize("app", APPS, ids=_id)
def test_findings_match_answer_key_exactly(app: dict[str, Any]) -> None:
    found = run_rules(load_review(SAMPLE, app["sub_id"], app["app_no"]))
    assert {_signature(f) for f in found} == _expected(app)
    assert len(found) == len(_expected(app)), "each planted error is reported once"


def test_answer_key_covers_the_brief() -> None:
    planted = {p["error"] for app in APPS for p in app["planted"]}
    assert {
        "math error",
        "wrong from-previous",
        "over-billed line",
        "billing on a pending CO",
        "missed retainage reduction",
        "G702 doesn't tie",
        "front-loaded general conditions",
    } <= planted
    assert len(APPS) == 15
    assert sum(app["clean"] for app in APPS) >= 3


@pytest.mark.parametrize("app", [a for a in APPS if a["clean"]], ids=_id)
def test_clean_applications_have_zero_findings(app: dict[str, Any]) -> None:
    assert run_rules(load_review(SAMPLE, app["sub_id"], app["app_no"])) == []
