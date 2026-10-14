"""Pydantic models for every input row, the G702 summary, and every finding.

Money is always `Decimal` rounded to cents. Percents are `Decimal` in percent units (10 = 10%).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from .money import ZERO, parse_decimal, parse_money


def _blank_to_none(raw: object) -> object:
    return None if isinstance(raw, str) and not raw.strip() else raw


Money = Annotated[Decimal, BeforeValidator(parse_money)]
Percent = Annotated[Decimal, BeforeValidator(parse_decimal), Field(ge=0, le=100)]
OptionalPercent = Annotated[Percent | None, BeforeValidator(_blank_to_none)]


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class COStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# ---------------------------------------------------------------- inputs


class ProjectInfo(_Row):
    """Optional project.json. Used for headings in the workbook and memo."""

    name: str
    general_contractor: str = "General Contractor"


class Subcontract(_Row):
    sub_id: str = Field(min_length=1)
    sub_name: str
    trade: str
    original_contract_sum: Money
    retainage_pct: Percent
    retainage_reduced_pct: OptionalPercent = None
    retainage_reduction_threshold_pct: OptionalPercent = None

    @model_validator(mode="after")
    def _reduction_is_complete(self) -> Subcontract:
        reduced, threshold = self.retainage_reduced_pct, self.retainage_reduction_threshold_pct
        if (reduced is None) != (threshold is None):
            raise ValueError(
                "retainage_reduced_pct and retainage_reduction_threshold_pct must both be set "
                "or both be blank"
            )
        if reduced is not None and reduced > self.retainage_pct:
            raise ValueError("retainage_reduced_pct cannot exceed retainage_pct")
        return self


class SovLine(_Row):
    sub_id: str
    line_no: str = Field(min_length=1)
    description: str
    scheduled_value: Money


class ChangeOrder(_Row):
    co_no: str = Field(min_length=1)
    sub_id: str
    description: str
    amount: Money  # may be negative (deductive change order)
    status: COStatus
    sov_line_no: str = Field(min_length=1)
    # The log shows today's status. The date lets a historical app be reviewed against the
    # COs that were approved as of that app's period end. Blank = approved for every period.
    approved_date: Annotated[dt.date | None, BeforeValidator(_blank_to_none)] = None

    @model_validator(mode="after")
    def _date_only_when_approved(self) -> ChangeOrder:
        if self.approved_date is not None and self.status is not COStatus.APPROVED:
            raise ValueError(f"approved_date is set but status is {self.status.value}")
        return self

    def approved_as_of(self, as_of: dt.date) -> bool:
        return self.status is COStatus.APPROVED and (
            self.approved_date is None or self.approved_date <= as_of
        )


# G703 column letter -> field name. Used for findings and for highlighting cells in Excel.
G703_COLUMNS: dict[str, str] = {
    "A": "item_no",
    "B": "description",
    "C": "scheduled_value",
    "D": "from_previous",
    "E": "this_period",
    "F": "materials_stored",
    "G": "total_completed_stored",
    "pct": "pct_complete",
    "H": "balance_to_finish",
    "I": "retainage",
}


class G703Row(_Row):
    """One continuation-sheet line: what the sub submitted, or what the GC certified."""

    item_no: str = Field(min_length=1)
    description: str
    scheduled_value: Money  # C
    from_previous: Money  # D: work completed on previous applications (prior D + E)
    this_period: Money  # E: work completed this period
    materials_stored: Money = ZERO  # F: presently stored, not in D or E
    total_completed_stored: Money  # G = D + E + F
    pct_complete: Annotated[Decimal, BeforeValidator(parse_decimal)]  # G / C, may exceed 100
    balance_to_finish: Money  # H = C - G
    retainage: Money  # I

    @property
    def work_completed(self) -> Decimal:
        """D + E: what carries forward to the next application's column D."""
        return self.from_previous + self.this_period


# G702 line number -> (field name, label as printed on the form)
G702_LINES: dict[int, tuple[str, str]] = {
    1: ("original_contract_sum", "Original Contract Sum"),
    2: ("net_change_by_change_orders", "Net Change by Change Orders"),
    3: ("contract_sum_to_date", "Contract Sum to Date"),
    4: ("total_completed_and_stored", "Total Completed & Stored to Date"),
    5: ("retainage", "Retainage"),
    6: ("total_earned_less_retainage", "Total Earned Less Retainage"),
    7: ("less_previous_certificates", "Less Previous Certificates for Payment"),
    8: ("current_payment_due", "Current Payment Due"),
    9: ("balance_to_finish_including_retainage", "Balance to Finish, Including Retainage"),
}


class G702Summary(_Row):
    sub_id: str
    application_no: int = Field(ge=1)
    period_to: dt.date
    original_contract_sum: Money
    net_change_by_change_orders: Money
    contract_sum_to_date: Money
    total_completed_and_stored: Money
    retainage: Money
    total_earned_less_retainage: Money
    less_previous_certificates: Money
    current_payment_due: Money
    balance_to_finish_including_retainage: Money

    def line(self, number: int) -> Decimal:
        value: Decimal = getattr(self, G702_LINES[number][0])
        return value


# ---------------------------------------------------------------- certifiable


class CertifiableLine(BaseModel):
    """What the GC can certify on one line, rebuilt from trusted inputs."""

    model_config = ConfigDict(frozen=True)

    item_no: str
    description: str
    scheduled_value: Decimal  # SOV + approved COs as of the period end
    from_previous: Decimal  # prior certificate D + E
    this_period: Decimal  # as claimed, reduced if the line would exceed C
    materials_stored: Decimal
    total_completed_stored: Decimal
    retainage: Decimal


# ---------------------------------------------------------------- review config + findings


class ReviewConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    front_load_threshold_pts: Decimal = Decimal("25")
    # Mobilization and bonds/insurance are deliberately absent: they are legitimately billed early.
    front_load_keywords: tuple[str, ...] = (
        "general conditions",
        "supervision",
        "project management",
    )
    tolerance: Decimal = Decimal("0.01")


class Finding(BaseModel):
    """One problem on a pay app. `dollar_impact` = claimed - correct (positive = over-claimed)."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    rule_name: str
    severity: Severity
    message: str
    item_no: str | None = None  # G703 line, None for app-level findings
    column: str | None = None  # G703 column letter ("G", "pct") or G702 line ("L4")
    expected: Decimal | None = None
    actual: Decimal | None = None
    dollar_impact: Decimal = ZERO


# ---------------------------------------------------------------- review result


class G702Line(BaseModel):
    """One G702 line: what the sub claimed next to what the GC can certify."""

    model_config = ConfigDict(frozen=True)

    line: int
    label: str
    claimed: Decimal
    certifiable: Decimal

    @computed_field  # type: ignore[prop-decorator]
    @property
    def variance(self) -> Decimal:
        return self.claimed - self.certifiable


class ReviewResult(BaseModel):
    """Everything the review produced. Serialized as findings.json."""

    model_config = ConfigDict(frozen=True)

    project: str
    general_contractor: str
    sub_id: str
    sub_name: str
    trade: str
    application_no: int
    period_to: dt.date
    contract_sum_to_date: Decimal
    certifiable_pct_complete: Decimal
    retainage_rate: Decimal
    g702: list[G702Line]
    certifiable_lines: list[CertifiableLine]
    findings: list[Finding]

    def g702_line(self, number: int) -> G702Line:
        return next(line for line in self.g702 if line.line == number)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def claimed_payment_due(self) -> Decimal:
        return self.g702_line(8).claimed

    @computed_field  # type: ignore[prop-decorator]
    @property
    def certifiable_payment_due(self) -> Decimal:
        return self.g702_line(8).certifiable

    @computed_field  # type: ignore[prop-decorator]
    @property
    def variance(self) -> Decimal:
        """Claimed minus certifiable current payment due. Positive = the sub over-claimed."""
        return self.g702_line(8).variance

    def count(self, severity: Severity) -> int:
        return sum(f.severity is severity for f in self.findings)
