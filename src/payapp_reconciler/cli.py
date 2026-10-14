"""payapp-reconcile <project_dir> --sub <id> --app <n> [--memo] [--out out/]"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from .excel import write_workbook
from .loaders import LoadError
from .memo import draft_memo
from .models import ReviewConfig, ReviewResult, Severity
from .money import fmt_money, fmt_pct
from .review import review_application, write_findings_json

SEVERITY_STYLE = {Severity.ERROR: "bold red", Severity.WARNING: "yellow", Severity.INFO: "cyan"}


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env support: KEY=VALUE lines; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="payapp-reconcile",
        description="Review a subcontractor pay application (AIA G702/G703) against the "
        "subcontract, SOV, change order log and the GC's prior certificate.",
    )
    parser.add_argument("project_dir", type=Path, help="project folder (see data/sample)")
    parser.add_argument("--sub", required=True, help="subcontractor id from subcontracts.csv")
    parser.add_argument("--app", required=True, type=int, help="application number to review")
    parser.add_argument("--memo", action="store_true", help="also draft memo.md to the sub")
    parser.add_argument("--out", type=Path, default=Path("out"), help="output folder (out/)")
    parser.add_argument(
        "--front-load-threshold",
        type=Decimal,
        default=ReviewConfig().front_load_threshold_pts,
        metavar="PTS",
        help="flag general conditions billed this many %% points ahead of the work (25)",
    )
    return parser.parse_args(argv)


def _money_cell(value: Decimal, variance: bool = False) -> str:
    text = fmt_money(value)
    if variance and value:
        return f"[bold red]{text}[/]" if value > 0 else f"[bold green]{text}[/]"
    return text


def render(console: Console, result: ReviewResult) -> None:
    console.print(
        f"[bold]{result.project}[/]  |  {result.sub_name} ({result.sub_id}), {result.trade}\n"
        f"Pay Application No. {result.application_no}, period ending {result.period_to}  |  "
        f"certifiable {fmt_pct(result.certifiable_pct_complete)} complete, retainage "
        f"{fmt_pct(result.retainage_rate)}"
    )

    g702 = Table(title="G702: claimed vs. certifiable", box=box.SIMPLE_HEAD, title_justify="left")
    g702.add_column("Line", justify="right")
    g702.add_column("Description")
    for name in ("Claimed", "Certifiable", "Variance"):
        g702.add_column(name, justify="right")
    for line in result.g702:
        style = "bold" if line.line == 8 else None
        g702.add_row(
            str(line.line),
            line.label,
            _money_cell(line.claimed),
            _money_cell(line.certifiable),
            _money_cell(line.variance, variance=True),
            style=style,
        )
    console.print(g702)

    counts = ", ".join(f"{result.count(s)} {s.value}" for s in Severity)
    if not result.findings:
        console.print("[bold green]No findings: the application ties out.[/]\n")
        return
    findings = Table(title=f"Findings ({counts})", box=box.SIMPLE_HEAD, title_justify="left")
    findings.add_column("Rule")
    findings.add_column("Severity")
    findings.add_column("Item")
    findings.add_column("Col")
    findings.add_column("Impact", justify="right")
    findings.add_column("Finding", ratio=1)
    for f in result.findings:
        findings.add_row(
            f.rule_id,
            f"[{SEVERITY_STYLE[f.severity]}]{f.severity.value}[/]",
            f.item_no or "-",
            f.column or "-",
            fmt_money(f.dollar_impact) if f.dollar_impact else "-",
            f.message,
        )
    console.print(findings)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    console = Console()
    config = ReviewConfig(front_load_threshold_pts=args.front_load_threshold)
    try:
        review, result = review_application(args.project_dir, args.sub, args.app, config)
    except LoadError as err:
        console.print(f"[bold red]Input error:[/] {err}")
        return 2

    render(console, result)
    out_dir = args.out / f"{args.sub}_app_{args.app}"
    write_findings_json(result, out_dir / "findings.json")
    write_workbook(review, result, out_dir / "review.xlsx")
    written = ["findings.json", "review.xlsx"]
    if args.memo:
        memo = draft_memo(result)
        (out_dir / "memo.md").write_text(memo.text, encoding="utf-8", newline="\n")
        note = f" ({memo.source}{': ' + memo.note if memo.note else ''})"
        written.append(f"memo.md{note}")
    console.print(f"Wrote {out_dir.as_posix()}/: " + ", ".join(written))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
