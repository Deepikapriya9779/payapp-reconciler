"""Run one review end to end: load, check, rebuild the certifiable amount, write findings.json."""

from __future__ import annotations

from pathlib import Path

from .certify import certifiable_g702
from .loaders import ReviewInput, load_review
from .models import G702_LINES, G702Line, ReviewConfig, ReviewResult
from .rules import apply_rules, build_context


def review_input(review: ReviewInput, config: ReviewConfig | None = None) -> ReviewResult:
    ctx = build_context(review, config)
    cert = ctx.certifiable
    certifiable = certifiable_g702(review, cert)
    sub = review.subcontract
    return ReviewResult(
        project=review.project.name,
        general_contractor=review.project.general_contractor,
        sub_id=sub.sub_id,
        sub_name=sub.sub_name,
        trade=sub.trade,
        application_no=review.app_no,
        period_to=review.period_to,
        contract_sum_to_date=cert.contract_sum_to_date,
        certifiable_pct_complete=cert.pct_complete,
        retainage_rate=cert.retainage_rate,
        g702=[
            G702Line(
                line=n,
                label=label,
                claimed=review.g702.line(n),
                certifiable=certifiable[n],
            )
            for n, (_, label) in G702_LINES.items()
        ],
        certifiable_lines=cert.lines,
        findings=apply_rules(ctx),
    )


def review_application(
    project_dir: Path, sub_id: str, app_no: int, config: ReviewConfig | None = None
) -> tuple[ReviewInput, ReviewResult]:
    review = load_review(project_dir, sub_id, app_no)
    return review, review_input(review, config)


def write_findings_json(result: ReviewResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
