"""Memo drafting: template fallback, LLM path with a fake client, and the numeric guardrail."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from payapp_reconciler.memo import draft_memo, template_memo, unverified_amounts
from payapp_reconciler.models import ReviewResult
from payapp_reconciler.review import review_application

SAMPLE = Path(__file__).parents[1] / "data" / "sample"
KEY_ENV = {"ANTHROPIC_API_KEY": "test-key-not-real"}


def result_for(sub: str, app: int) -> ReviewResult:
    return review_application(SAMPLE, sub, app)[1]


class FakeClient:
    """Stands in for anthropic.Anthropic(): records the request, returns a canned reply."""

    def __init__(
        self, text: str = "", stop_reason: str = "end_turn", error: Exception | None = None
    ):
        self.requests: list[dict[str, Any]] = []
        self._text, self._stop, self._error = text, stop_reason, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        if self._error:
            raise self._error
        return SimpleNamespace(
            stop_reason=self._stop, content=[SimpleNamespace(type="text", text=self._text)]
        )


def test_template_memo_lists_corrections_and_payment_summary() -> None:
    memo = template_memo(result_for("mech", 4))
    assert "**To:** Sonoran Air Mechanical" in memo
    assert "## Corrections required" in memo
    assert "1. Line CO-03 (Added VAV zone, 2nd floor east): CO-03 is pending." in memo
    assert "| 8. Current Payment Due | $458,586.00 | $449,798.50 |" in memo
    assert "Backup or clarification" not in memo  # no warnings on this app


def test_template_memo_for_a_clean_application() -> None:
    memo = template_memo(result_for("mech", 1))
    assert "It ties out" in memo and "$171,990.00 as submitted" in memo
    assert "Corrections required" not in memo


def test_template_memo_warnings_section() -> None:
    assert "## Backup or clarification needed" in template_memo(result_for("mech", 3))


def test_mock_mode_and_missing_key_use_template() -> None:
    result = result_for("mech", 4)
    assert draft_memo(result, env={"LLM_MODE": "mock", **KEY_ENV}).note == "LLM_MODE=mock"
    memo = draft_memo(result, env={})
    assert (memo.source, memo.note) == ("template", "ANTHROPIC_API_KEY not set")


def test_llm_memo_is_used_when_every_amount_checks_out() -> None:
    result = result_for("mech", 4)
    text = (
        "# Memo\n\nRequested $458,586.00; certifiable $449,798.50. Remove the $9,250.00 on CO-03."
    )
    client = FakeClient(text)
    memo = draft_memo(result, client=client, env={"ANTHROPIC_MODEL": "claude-sonnet-5-5"})
    assert memo.source == "llm" and memo.text.startswith("# Memo")

    [request] = client.requests
    assert request["model"] == "claude-sonnet-5-5"
    assert request["fallbacks"] == "default"
    assert "R05" in request["messages"][0]["content"]  # the findings JSON is the input
    assert "certifiable_lines" not in request["messages"][0]["content"]


def test_default_model_and_no_fallbacks_for_other_models() -> None:
    result = result_for("mech", 1)
    client = FakeClient("The application ties out.")
    draft_memo(result, client=client, env={})
    assert client.requests[0]["model"] == "claude-sonnet-5-5"
    client = FakeClient("The application ties out.")
    draft_memo(result, client=client, env={"ANTHROPIC_MODEL": "claude-haiku-4-5"})
    assert "fallbacks" not in client.requests[0]


def test_guardrail_rejects_invented_amounts() -> None:
    result = result_for("mech", 4)
    client = FakeClient("You were overpaid by $12,345.67 overall.")
    memo = draft_memo(result, client=client, env={})
    assert memo.source == "template"
    assert "$12,345.67" in memo.note
    assert unverified_amounts("Variance $8,787.50 and $-8,787.50", result) == []


@pytest.mark.parametrize(
    "client",
    [
        FakeClient(error=RuntimeError("connection reset")),
        FakeClient("I can't help with that.", stop_reason="refusal"),
        FakeClient("   "),
    ],
    ids=["api-error", "refusal", "empty"],
)
def test_llm_failures_fall_back_to_template(client: FakeClient) -> None:
    result = result_for("mech", 4)
    memo = draft_memo(result, client=client, env={})
    assert memo.source == "template" and memo.note.startswith("LLM draft failed")
    assert memo.text == template_memo(result)


def test_real_client_is_built_from_the_key_without_calling_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic

    built: list[str] = []

    def fake_anthropic(api_key: str) -> FakeClient:
        built.append(api_key)
        return FakeClient("The application ties out.")

    monkeypatch.setattr(anthropic, "Anthropic", fake_anthropic)
    memo = draft_memo(result_for("mech", 1), env=KEY_ENV)
    assert built == ["test-key-not-real"] and memo.source == "llm"
