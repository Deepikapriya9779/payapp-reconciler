"""The memo back to the subcontractor.

The LLM only writes prose. It gets the structured findings, is told to copy every dollar figure
from them, and its draft is then checked: any dollar amount that does not appear in the findings
sends us back to the deterministic template. The template is also used when there is no API key,
when LLM_MODE=mock, or when the API call fails.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from .models import ReviewResult, Severity
from .money import fmt_money, fmt_pct, parse_money

DEFAULT_MODEL = "claude-sonnet-5-5"
# Models that accept server-side refusal fallbacks ("default" routing).
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DOLLAR_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")

SYSTEM_PROMPT = """\
You write memos from a general contractor's project accounting team to a subcontractor about \
their monthly pay application (AIA G702/G703). The memo tells the subcontractor exactly what to \
correct before the GC can certify payment.

Rules:
- Use only the facts in the JSON you are given. Every dollar amount you write must be copied \
exactly from the JSON. Never add, subtract, total or estimate amounts, and never invent figures.
- The JSON's findings are final. Do not second-guess them or add new ones.
- Plain, professional construction language. Be direct and courteous; no filler.
- Markdown. Start with a header block (To, From, Project, Re). Then one short paragraph, a \
numbered "Corrections required" list for error findings, a "Backup or clarification needed" list \
for warnings, a short G702 summary of requested vs. certifiable payment, and a closing line asking \
for a revised application. Leave out empty sections.
- If there are no findings, write a brief note that the application ties out.
"""


@dataclass(frozen=True)
class Memo:
    text: str
    source: Literal["llm", "template"]
    note: str = ""  # why the template was used, when it was


# ---------------------------------------------------------------- template


def template_memo(result: ReviewResult) -> str:
    """Deterministic memo built straight from the findings."""
    n = result.application_no
    lines = [
        f"# Pay Application No. {n} Review",
        "",
        f"**To:** {result.sub_name}  ",
        f"**From:** Project Accounting, {result.general_contractor}  ",
        f"**Project:** {result.project}  ",
        f"**Re:** Pay Application No. {n} ({result.trade}), period ending {result.period_to}",
        "",
    ]
    errors = [f for f in result.findings if f.severity is Severity.ERROR]
    warnings = [f for f in result.findings if f.severity is Severity.WARNING]
    infos = [f for f in result.findings if f.severity is Severity.INFO]
    previous = f" and our certificate for Application No. {n - 1}" if n > 1 else ""

    if not result.findings:
        lines += [
            f"We reviewed Application No. {n} against the subcontract, the approved schedule of "
            f"values and change orders{previous}. It ties out, and we will process the current "
            f"payment due of {fmt_money(result.certifiable_payment_due)} as submitted.",
        ]
        return "\n".join(lines) + "\n"

    lines += [
        f"We reviewed Application No. {n} against the subcontract, the approved schedule of "
        f"values and change orders{previous}. The items below need to be addressed before we "
        "can certify the application as submitted.",
        "",
    ]
    if errors:
        lines += ["## Corrections required", ""]
        lines += [f"{i}. {f.message}" for i, f in enumerate(errors, start=1)]
        lines.append("")
    if warnings:
        lines += ["## Backup or clarification needed", ""]
        lines += [f"- {f.message}" for f in warnings]
        lines.append("")
    if infos:
        lines += ["## Minor items", ""]
        lines += [f"- {f.message}" for f in infos]
        lines.append("")

    lines += [
        "## Payment summary",
        "",
        "| G702 line | Requested | Certifiable |",
        "|---|---:|---:|",
    ]
    for number in (4, 5, 6, 7, 8):
        g = result.g702_line(number)
        lines.append(
            f"| {number}. {g.label} | {fmt_money(g.claimed)} | {fmt_money(g.certifiable)} |"
        )
    lines += [
        "",
        f"Certifiable work is {fmt_pct(result.certifiable_pct_complete)} complete with "
        f"retainage at {fmt_pct(result.retainage_rate)}. The certifiable amount accepts your "
        "reported progress for this period; the PM's field verification still applies.",
        "",
        "Please revise the G702 and G703 and resubmit. Contact project accounting with any "
        "questions.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- LLM


def memo_input(result: ReviewResult) -> dict[str, Any]:
    """The facts the LLM may use: header, G702 comparison and findings (no line detail)."""
    data = result.model_dump(mode="json", exclude={"certifiable_lines"})
    for finding in data["findings"]:
        finding.pop("expected", None)
        finding.pop("actual", None)
    return data


def allowed_amounts(result: ReviewResult) -> set[Decimal]:
    """Every dollar figure the memo may cite: anything the review itself produced."""
    amounts = {result.contract_sum_to_date}
    for g in result.g702:
        amounts |= {g.claimed, g.certifiable, g.variance}
    for f in result.findings:
        amounts |= {v for v in (f.expected, f.actual, f.dollar_impact) if v is not None}
        amounts |= {parse_money(m) for m in DOLLAR_RE.findall(f.message)}
    return {abs(a) for a in amounts}


def unverified_amounts(text: str, result: ReviewResult) -> list[str]:
    allowed = allowed_amounts(result)
    return [m for m in DOLLAR_RE.findall(text) if parse_money(m) not in allowed]


def llm_memo(result: ReviewResult, client: Any, model: str) -> str:
    """Ask Claude to draft the memo prose. Returns the text, or raises ValueError on refusal."""
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": "Draft the memo from this review:\n\n"
                + json.dumps(memo_input(result), indent=2),
            }
        ],
    }
    if model in FALLBACK_MODELS:
        request |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    response = client.beta.messages.create(**request)
    if response.stop_reason == "refusal":
        raise ValueError("the model declined the request")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ValueError("the model returned no text")
    return text + "\n"


def draft_memo(
    result: ReviewResult, client: Any | None = None, env: Mapping[str, str] | None = None
) -> Memo:
    """LLM memo when configured and verifiable, otherwise the template."""
    env = os.environ if env is None else env
    template = template_memo(result)
    if env.get("LLM_MODE", "").lower() == "mock":
        return Memo(template, "template", "LLM_MODE=mock")
    if client is None:
        if not env.get("ANTHROPIC_API_KEY"):
            return Memo(template, "template", "ANTHROPIC_API_KEY not set")
        import anthropic  # imported lazily: the template path needs no SDK

        client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])
    model = env.get("ANTHROPIC_MODEL") or DEFAULT_MODEL

    try:
        text = llm_memo(result, client, model)
    except Exception as err:  # any SDK, network or refusal failure falls back to the template
        return Memo(template, "template", f"LLM draft failed ({type(err).__name__}: {err})")

    unknown = unverified_amounts(text, result)
    if unknown:
        return Memo(
            template,
            "template",
            f"LLM draft cited amounts not in the findings ({', '.join(unknown)}); "
            "used the template instead",
        )
    return Memo(text, "llm")
