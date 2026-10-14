"""Read a project folder into validated models. Bad input fails loudly, naming the file and row."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .models import ChangeOrder, G702Summary, G703Row, SovLine, Subcontract

M = TypeVar("M", bound=BaseModel)


class LoadError(Exception):
    """Input data is missing or invalid. The message names the file (and row, for CSVs)."""


@dataclass(frozen=True)
class ReviewInput:
    """Everything needed to review one subcontractor's application."""

    project_dir: Path
    app_no: int
    subcontract: Subcontract
    sov: list[SovLine]
    change_orders: list[ChangeOrder]
    prior_certified: list[G703Row] | None  # None on application 1
    submitted: list[G703Row]
    g702: G702Summary


def _describe(err: ValidationError) -> str:
    parts = []
    for e in err.errors():
        field = ".".join(str(p) for p in e["loc"]) or "row"
        parts.append(f"{field}: {e['msg']}")
    return "; ".join(parts)


def read_csv(path: Path, model: type[M], label: str | None = None) -> list[M]:
    """Parse every row of `path` into `model`. Row numbers match the spreadsheet (header = 1)."""
    label = label or str(path)
    if not path.is_file():
        raise LoadError(f"{label}: file not found")
    rows: list[M] = []
    with path.open(newline="", encoding="utf-8-sig") as f:  # -sig strips Excel's BOM
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise LoadError(f"{label}: file is empty")
        for row_no, raw in enumerate(reader, start=2):
            if None in raw:
                raise LoadError(f"{label} row {row_no}: more values than header columns")
            try:
                rows.append(model.model_validate(raw))
            except ValidationError as err:
                raise LoadError(f"{label} row {row_no}: {_describe(err)}") from None
    return rows


def read_g702(path: Path, label: str | None = None) -> G702Summary:
    label = label or str(path)
    if not path.is_file():
        raise LoadError(f"{label}: file not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except json.JSONDecodeError as err:
        raise LoadError(f"{label}: invalid JSON ({err})") from None
    try:
        return G702Summary.model_validate(data)
    except ValidationError as err:
        raise LoadError(f"{label}: {_describe(err)}") from None


def load_review(project_dir: Path, sub_id: str, app_no: int) -> ReviewInput:
    """Load the trusted contract data, the prior certificate and the submitted application."""
    project_dir = Path(project_dir)

    def read(rel: str, model: type[M]) -> list[M]:
        return read_csv(project_dir / rel, model, label=rel)

    subcontracts = {s.sub_id: s for s in read("subcontracts.csv", Subcontract)}
    if sub_id not in subcontracts:
        known = ", ".join(sorted(subcontracts)) or "none"
        raise LoadError(f"subcontracts.csv: no subcontract with sub_id {sub_id!r} (known: {known})")

    sov = [line for line in read("sov.csv", SovLine) if line.sub_id == sub_id]
    if not sov:
        raise LoadError(f"sov.csv: no schedule-of-values lines for sub_id {sub_id!r}")
    change_orders = [co for co in read("change_orders.csv", ChangeOrder) if co.sub_id == sub_id]

    prior = None
    if app_no > 1:
        # The GC's certificate, not the sub's own prior submission, is the source of truth.
        prior = read(f"certified/{sub_id}_app_{app_no - 1}.csv", G703Row)

    submitted = read(f"submitted/{sub_id}_app_{app_no}.csv", G703Row)
    g702_rel = f"submitted/{sub_id}_app_{app_no}_g702.json"
    g702 = read_g702(project_dir / g702_rel, label=g702_rel)
    if g702.sub_id != sub_id or g702.application_no != app_no:
        raise LoadError(
            f"{g702_rel}: header says sub {g702.sub_id!r} application {g702.application_no}, "
            f"expected sub {sub_id!r} application {app_no}"
        )

    return ReviewInput(
        project_dir=project_dir,
        app_no=app_no,
        subcontract=subcontracts[sub_id],
        sov=sov,
        change_orders=change_orders,
        prior_certified=prior,
        submitted=submitted,
        g702=g702,
    )
