"""Tests for Day 9 deterministic semantic role inference."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.ingestion.schemas import ParsedSheet, ParsedWorkbook
from backend.understanding.profiling import add_profiles
from backend.understanding.schemas import InferredField

client = TestClient(app)


def infer_fields(
    columns: list[str], rows: Sequence[Sequence[object]]
) -> dict[str, InferredField]:
    normalized = [[None if cell is None else str(cell) for cell in row] for row in rows]
    sheet = ParsedSheet(
        name="t",
        header_row_number=1,
        columns=columns,
        rows=normalized,
        data_row_count=len(normalized),
    )
    workbook = ParsedWorkbook(file_name="f.xlsx", file_type="xlsx", sheets=[sheet])
    return {
        field.column: field
        for field in add_profiles(workbook).sheets[0].inferred_fields
    }


# ---------- identifier ----------


def test_unique_code_column_is_identifier():
    rows = [[f"SO-{1000 + i}"] for i in range(6)]
    field = infer_fields(["订单编号"], rows)["订单编号"]
    assert field.role == "identifier"
    assert field.role_confidence == 0.95 and field.role_needs_review is False
    assert "unique_values" in field.role_signals


def test_unique_name_column_is_identifier():
    field = infer_fields(
        ["客户名称"], [["杭州云栖"], ["深圳前海"], ["成都锦江"], ["北京海淀"]]
    )["客户名称"]
    assert field.role == "identifier" and field.role_confidence >= 0.85


def test_duplicated_name_is_dimension_not_identifier():
    # customer_name on the order side: same customer appears on many orders.
    rows = [["杭州云栖"], ["杭州云栖"], ["深圳前海"], ["成都锦江"]]
    field = infer_fields(["客户名称"], rows)["客户名称"]
    assert field.role == "dimension"
    assert field.role_confidence == 0.85


def test_unique_phone_is_text_never_identifier():
    rows = [["13800001111"], ["13900002222"], ["13700003333"]]
    field = infer_fields(["联系电话"], rows)["联系电话"]
    assert field.type == "phone"
    assert field.role == "text"


def test_unique_string_without_key_name_is_low_confidence_identifier():
    field = infer_fields(["某列"], [["alpha"], ["beta"], ["gamma"], ["delta"]])["某列"]
    assert field.role == "identifier"
    assert field.role_confidence == 0.75 and field.role_needs_review is True


def test_unique_numbers_without_key_name_stay_measure_but_flagged():
    rows = [[10], [20], [30], [40], [50]]
    field = infer_fields(["某数值"], rows)["某数值"]
    assert field.type == "number"
    assert field.role == "measure" and field.role_needs_review is True


def test_unique_numeric_id_with_keyword_is_identifier():
    rows = [[1001], [1002], [1003], [1004]]
    field = infer_fields(["订单号"], rows)["订单号"]
    assert field.type == "number"
    assert field.role == "identifier" and field.role_confidence >= 0.85


# ---------- time / measure ----------


def test_date_is_time_and_money_is_measure():
    rows = [
        ["2026-07-03", 87350.5],
        ["2026-08-15", 12000],
        ["2026-09-01", 6400],
        ["2026-09-20", 3200],
    ]
    fields = infer_fields(["下单日期", "金额"], rows)
    assert fields["下单日期"].role == "time"
    assert fields["金额"].role == "measure"
    assert fields["金额"].role_needs_review is False


# ---------- enum vs dimension ----------


def test_wordy_enum_is_dimension_but_code_enum_is_enum_role():
    region_rows = [["华东"], ["华南"], ["华北"], ["华东"], ["华南"], ["华北"]]
    region = infer_fields(["区域"], region_rows)["区域"]
    assert region.type == "enum" and region.role == "dimension"

    status_rows = [[0], [1], [1], [0], [0], [1]]
    status = infer_fields(["状态"], status_rows)["状态"]
    assert status.type == "enum" and status.role == "enum"
    assert status.role_confidence == 0.75 and status.role_needs_review is True


# ---------- text ----------


def test_remark_column_is_text_by_name_and_length():
    by_name = infer_fields(
        ["备注"],
        [["客户要求加急处理，需提前一天联系"], [None], ["发票随货同行，地址见客户资料"]],
    )["备注"]
    assert by_name.role == "text" and by_name.role_confidence >= 0.85

    long_rows = [["这是一段没有关键词但明显很长的自由文本内容用于描述情况"]] * 4
    unnamed = infer_fields(["其他"], long_rows)["其他"]
    assert unnamed.role == "text"
    assert unnamed.role_confidence == 0.7 and unnamed.role_needs_review is True


def test_owner_short_string_is_dimension():
    rows = [["王经理"], ["李总"], ["赵主管"], ["王经理"]]
    field = infer_fields(["负责人"], rows)["负责人"]
    assert field.role == "dimension" and field.role_confidence == 0.85


# ---------- empty ----------


def test_empty_column_role_is_zero_confidence_text():
    field = infer_fields(["空列"], [[None], [None], [None]])["空列"]
    assert field.role == "text"
    assert field.role_confidence == 0.0 and field.role_needs_review is True


# ---------- end to end ----------


def test_parse_endpoint_returns_roles_matching_design_example():
    csv_body = (
        "订单编号,客户名称,金额,下单日期,区域,联系电话,状态,负责人,备注\n"
        "SO-1001,杭州云栖科技,87350.5,2026-07-03,华东,13800001111,1,王经理,老客户\n"
        "SO-1002,深圳前海贸易,12000,2026-08-15,华南,13900002222,0,李总,新客户首单\n"
        "SO-1003,成都锦江商贸,6400,2026-09-01,华东,13700003333,1,王经理,老客户\n"
        "SO-1004,北京海淀贸易,3200,2026-09-20,华北,13600004444,0,赵主管,加急处理\n"
        "SO-1005,南京玄武商贸,9800,2026-10-02,华东,13500005555,1,王经理,老客户\n"
        "SO-1006,武汉江汉贸易,15600,2026-10-05,华南,13400006666,0,李总,新客户首单\n"
    ).encode()
    response = client.post(
        "/api/v1/ingestion/parse",
        files={"file": ("o.csv", csv_body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    roles = {
        field["column"]: field["role"]
        for field in response.json()["sheets"][0]["inferred_fields"]
    }
    assert roles == {
        "订单编号": "identifier",
        "客户名称": "identifier",  # unique within this small sheet
        "金额": "measure",
        "下单日期": "time",
        "区域": "dimension",
        "联系电话": "text",
        "状态": "enum",
        "负责人": "dimension",
        "备注": "text",
    }
