import json
from decimal import Decimal
from pathlib import Path

import pytest

from payapp_reconciler.loaders import LoadError, load_review, read_csv, read_g702
from payapp_reconciler.models import COStatus, G703Row, Subcontract

G703_HEADER = (
    "item_no,description,scheduled_value,from_previous,this_period,materials_stored,"
    "total_completed_stored,pct_complete,balance_to_finish,retainage\n"
)
G703_ROW = "01,Ductwork,100000.00,0.00,25000.00,0.00,25000.00,25.00,75000.00,2500.00\n"

G702 = {
    "sub_id": "mech",
    "application_no": 1,
    "period_to": "2026-01-31",
    "original_contract_sum": "100000.00",
    "net_change_by_change_orders": "0.00",
    "contract_sum_to_date": "100000.00",
    "total_completed_and_stored": "25000.00",
    "retainage": "2500.00",
    "total_earned_less_retainage": "22500.00",
    "less_previous_certificates": "0.00",
    "current_payment_due": "22500.00",
    "balance_to_finish_including_retainage": "77500.00",
}


def write_project(root: Path, app_no: int = 1, with_prior: bool = True) -> Path:
    (root / "submitted").mkdir(parents=True)
    (root / "certified").mkdir()
    (root / "subcontracts.csv").write_text(
        "sub_id,sub_name,trade,original_contract_sum,retainage_pct,"
        "retainage_reduced_pct,retainage_reduction_threshold_pct\n"
        "mech,Test Mechanical,Mechanical,100000.00,10,5,50\n"
        "dryw,Test Drywall,Drywall,50000.00,10,,\n"
    )
    (root / "sov.csv").write_text(
        "sub_id,line_no,description,scheduled_value\n"
        "mech,01,Ductwork,100000.00\n"
        "dryw,01,Framing,50000.00\n"
    )
    (root / "change_orders.csv").write_text(
        "co_no,sub_id,description,amount,status,sov_line_no\n"
        "CO-01,mech,Added duct,5000.00,pending,CO-01\n"
    )
    (root / f"submitted/mech_app_{app_no}.csv").write_text(G703_HEADER + G703_ROW)
    (root / f"submitted/mech_app_{app_no}_g702.json").write_text(
        json.dumps(G702 | {"application_no": app_no})
    )
    if with_prior and app_no > 1:
        (root / f"certified/mech_app_{app_no - 1}.csv").write_text(G703_HEADER + G703_ROW)
    return root


def test_load_review_happy_path(tmp_path: Path) -> None:
    review = load_review(write_project(tmp_path, app_no=2), "mech", 2)
    assert review.subcontract.retainage_reduced_pct == Decimal("5")
    assert [line.line_no for line in review.sov] == ["01"]  # dryw lines filtered out
    assert review.change_orders[0].status is COStatus.PENDING
    assert review.prior_certified is not None
    assert review.prior_certified[0].work_completed == Decimal("25000.00")
    assert review.g702.current_payment_due == Decimal("22500.00")


def test_app_one_has_no_prior_certificate(tmp_path: Path) -> None:
    assert load_review(write_project(tmp_path), "mech", 1).prior_certified is None


def test_missing_prior_certificate_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match=r"certified/mech_app_1\.csv: file not found"):
        load_review(write_project(tmp_path, app_no=2, with_prior=False), "mech", 2)


def test_unknown_sub(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match="no subcontract with sub_id 'elec'"):
        load_review(write_project(tmp_path), "elec", 1)


def test_bad_cell_names_file_row_and_field(tmp_path: Path) -> None:
    path = tmp_path / "app.csv"
    path.write_text(G703_HEADER + G703_ROW + G703_ROW.replace("25000.00,0.00,25000", "25k,0.00,1"))
    with pytest.raises(LoadError, match=r"app\.csv row 3: this_period: .*not a number"):
        read_csv(path, G703Row)


def test_extra_column_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "app.csv"
    path.write_text(G703_HEADER.replace("\n", ",notes\n") + G703_ROW.replace("\n", ",hi\n"))
    with pytest.raises(LoadError, match="row 2: notes: Extra inputs are not permitted"):
        read_csv(path, G703Row)


def test_too_many_values_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "app.csv"
    path.write_text(G703_HEADER + G703_ROW.replace("\n", ",oops\n"))
    with pytest.raises(LoadError, match="row 2: more values than header columns"):
        read_csv(path, G703Row)


def test_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "app.csv"
    path.write_text("")
    with pytest.raises(LoadError, match="file is empty"):
        read_csv(path, G703Row)


def test_excel_bom_and_currency_formatting_are_accepted(tmp_path: Path) -> None:
    path = tmp_path / "app.csv"
    row = '01,Ductwork,"$100,000.00",0,"$25,000.00",0,25000,25%,75000,2500\n'
    path.write_text("\ufeff" + G703_HEADER + row, encoding="utf-8")
    [line] = read_csv(path, G703Row)
    assert line.scheduled_value == Decimal("100000.00")
    assert line.materials_stored == Decimal("0.00")
    assert line.pct_complete == Decimal("25")


def test_g702_json_numbers_parse_as_decimal(tmp_path: Path) -> None:
    path = tmp_path / "g702.json"
    path.write_text(json.dumps(G702).replace('"25000.00"', "25000.10"))
    # JSON numbers are parsed as Decimal, never float, so the cents survive.
    assert read_g702(path).total_completed_and_stored == Decimal("25000.10")


def test_g702_invalid_json_and_missing_field(tmp_path: Path) -> None:
    path = tmp_path / "g702.json"
    path.write_text("{not json")
    with pytest.raises(LoadError, match="invalid JSON"):
        read_g702(path)
    path.write_text(json.dumps({k: v for k, v in G702.items() if k != "retainage"}))
    with pytest.raises(LoadError, match="retainage: Field required"):
        read_g702(path)


def test_g702_header_must_match_requested_app(tmp_path: Path) -> None:
    root = write_project(tmp_path)
    (root / "submitted/mech_app_1_g702.json").write_text(json.dumps(G702 | {"sub_id": "elec"}))
    with pytest.raises(LoadError, match="header says sub 'elec'"):
        load_review(root, "mech", 1)


@pytest.mark.parametrize(
    ("reduced", "threshold", "message"),
    [("5", "", "must both be set"), ("12", "50", "cannot exceed")],
)
def test_subcontract_retainage_reduction_validation(
    reduced: str, threshold: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Subcontract.model_validate(
            {
                "sub_id": "x",
                "sub_name": "X",
                "trade": "T",
                "original_contract_sum": "1",
                "retainage_pct": "10",
                "retainage_reduced_pct": reduced,
                "retainage_reduction_threshold_pct": threshold,
            }
        )
