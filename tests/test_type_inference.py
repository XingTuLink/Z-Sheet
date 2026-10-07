"""Tests for Days 7-8 deterministic six-type field inference."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.ingestion.schemas import ParsedSheet, ParsedWorkbook
from backend.understanding.profiling import add_profiles
from backend.understanding.type_inference import (
    is_date,
    is_money,
    is_number,
    is_phone,
)

client = TestClient(app)


def infer_one(
    columns: list[str], rows: Sequence[Sequence[object]], sheet_name: str = "t"
):
    normalized = [[None if cell is None else str(cell) for cell in row] for row in rows]
    sheet = ParsedSheet(
        name=sheet_name,
        header_row_number=1,
        columns=columns,
        rows=normalized,
        data_row_count=len(normalized),
    )
    workbook = ParsedWorkbook(file_name="f.xlsx", file_type="xlsx", sheets=[sheet])
    return {field.column: field for field in add_profiles(workbook).sheets[0].inferred_fields}


# ---------- value-level parsers ----------


def test_value_parsers():
    assert is_number("123") and is_number("-0.5") and is_number("1,234,567.89")
    assert not is_number("SO-1001") and not is_number("12元") and not is_number("true")
    assert is_money("¥8,735.50") and is_money("￥1200") and is_money("$1,200.00")
    assert is_money("87350.5元") and not is_money("87350.5")
    assert is_phone("13800001111") and is_phone("+8613800001111")
    assert is_phone("0571-88888888") and is_phone("010 66668888")
    assert not is_phone("12345")
    assert is_date("2026-07-03") and is_date("2026-07-03T00:00:00")
    assert is_date("2026/7/3") and is_date("2026年7月3日") and is_date("2026.07.03")
    assert not is_date("2026-13-40") and not is_date("7月3日")


# ---------- number / money ----------


def test_plain_number_column_is_high_confidence():
    field = infer_one(["数量"], [[1], ["2.5"], ["1,234"], [-9]])["数量"]
    assert field.type == "number"
    assert field.confidence >= 0.95 and field.needs_review is False


def test_money_via_currency_markers():
    field = infer_one(["x"], [["¥8,735.50"], ["￥12,000"], ["$200.00"]])["x"]
    assert field.type == "money"
    assert field.confidence >= 0.95
    assert "currency_symbol" in field.signals


def test_money_via_column_name_with_plain_numbers():
    field = infer_one(["订单金额"], [[87350.5], [12000], [6400]])["订单金额"]
    assert field.type == "money"
    assert field.confidence >= 0.85 and field.needs_review is False
    assert "name_keyword:money" in field.signals


# ---------- date / phone ----------


def test_date_column_multiple_formats():
    field = infer_one(
        ["下单日期"],
        [["2026-07-03"], ["2026-07-03T00:00:00"], ["2026/8/15"], ["2026年9月1日"]],
    )["下单日期"]
    assert field.type == "date"
    assert field.confidence >= 0.95
    assert "calendar_validated" in field.signals


def test_phone_column_beats_number_rule():
    # Bare 11-digit mobile numbers must not be swallowed by number inference.
    field = infer_one(["某列"], [["13800001111"], ["13900002222"], ["13700003333"]])["某列"]
    assert field.type == "phone"
    assert field.confidence >= 0.95


def test_phone_with_prefixes_and_separators():
    field = infer_one(
        ["联系电话"], [["+8613800001111"], ["0571-88888888"], ["010 66668888"]]
    )["联系电话"]
    assert field.type == "phone"
    assert "name_keyword:phone" in field.signals


# ---------- enum ----------


def test_enum_with_category_name_is_high_confidence():
    rows = [["华东"], ["华南"], ["华北"], ["西南"]] * 2
    field = infer_one(["区域"], rows)["区域"]
    assert field.type == "enum"
    assert field.confidence >= 0.85 and field.needs_review is False
    assert field.enum_values == ["华东", "华北", "华南", "西南"]


def test_enum_by_distribution_alone_needs_review():
    rows = [["红"], ["蓝"], ["红"], ["绿"], ["蓝"], ["红"]]
    field = infer_one(["颜色"], rows)["颜色"]
    assert field.type == "enum"
    assert field.confidence == 0.75 and field.needs_review is True


def test_numeric_status_column_with_category_name_becomes_enum():
    field = infer_one(["状态"], [[0], [1], [1], [0], [0], [1]])["状态"]
    assert field.type == "enum"
    assert field.confidence == 0.75 and field.needs_review is True
    assert field.enum_values == ["0", "1"]


def test_low_cardinality_numbers_without_category_name_stay_number():
    # Few distinct quantities without a category-style name must stay numeric.
    field = infer_one(["件数"], [[1], [2], [2], [1], [3], [1]])["件数"]
    assert field.type == "number"


def test_high_cardinality_text_is_not_enum():
    rows = [[f"SO-{1000 + i}"] for i in range(12)]
    field = infer_one(["订单编号"], rows)["订单编号"]
    assert field.type == "string"
    assert field.confidence >= 0.9 and field.enum_values == []


# ---------- string / mixed / empty ----------


def test_free_text_string_high_confidence():
    field = infer_one(
        ["客户名称"], [["杭州云栖科技"], ["深圳前海贸易"], ["成都锦江商贸"]]
    )["客户名称"]
    assert field.type == "string" and field.confidence >= 0.9


def test_mostly_dates_with_garbage_falls_to_medium_review():
    field = infer_one(
        ["d"], [["2026-01-01"], ["2026-02-01"], ["2026-03-01"], ["待定"], ["2026-05-01"]]
    )["d"]
    assert field.type == "date"
    assert 0.60 <= field.confidence < 0.85 and field.needs_review is True
    assert "mixed_values" in field.signals


def test_empty_column_is_zero_confidence_string():
    field = infer_one(["备注"], [[None], [None], [None]])["备注"]
    assert field.type == "string"
    assert field.confidence == 0.0 and field.needs_review is True
    assert "no_non_null_values" in field.signals


def test_empty_sheet_has_no_inference():
    sheet = ParsedSheet(name="空", is_empty=True)
    workbook = ParsedWorkbook(file_name="e.xlsx", file_type="xlsx", sheets=[sheet])
    assert add_profiles(workbook).sheets[0].inferred_fields == []


# ---------- end to end ----------


def test_parse_endpoint_returns_inferred_fields():
    csv_body = (
        "订单号,客户名称,金额,下单日期,区域,电话\n"
        "SO-1001,杭州云栖科技,87350.5,2026-07-03,华东,13800001111\n"
        "SO-1002,深圳前海贸易,12000,2026-08-15,华南,13900002222\n"
        "SO-1003,成都锦江商贸,6400,2026-09-01,华东,13700003333\n"
        "SO-1004,北京海淀贸易,3200,2026-09-20,华北,13600004444\n"
        "SO-1005,南京玄武商贸,9800,2026-10-02,华东,13500005555\n"
        "SO-1006,武汉江汉贸易,15600,2026-10-05,华南,13400006666\n"
        "SO-1007,西安雁塔贸易,4300,2026-10-06,华北,13300007777\n"
        "SO-1008,广州天河贸易,21000,2026-10-07,华东,13200008888\n"
    ).encode()
    response = client.post(
        "/api/v1/ingestion/parse",
        files={"file": ("o.csv", csv_body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    fields = {field["column"]: field for field in response.json()["sheets"][0]["inferred_fields"]}

    assert fields["订单号"]["type"] == "string"
    assert fields["客户名称"]["type"] == "string"
    assert fields["金额"]["type"] == "money"
    assert fields["下单日期"]["type"] == "date"
    assert fields["区域"]["type"] == "enum"
    assert fields["电话"]["type"] == "phone"
    assert fields["金额"]["needs_review"] is False
