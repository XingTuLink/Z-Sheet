"""Multi-file import: parse, merge preview, conflict choices and confirm."""

from __future__ import annotations

import io
import json

import openpyxl
from fastapi.testclient import TestClient

from backend.api.main import app
from tests.runtime_helpers import rows

client = TestClient(app)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook(sheets: list[tuple[str, list[list[object]]]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.worksheets[0])
    for name, table in sheets:
        worksheet = wb.create_sheet(name)
        for row in table:
            worksheet.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _file_a() -> tuple[str, bytes]:
    return (
        "华北销售.xlsx",
        _workbook(
            [
                (
                    "客户",
                    [
                        ["客户名称", "区域", "联系电话"],
                        ["杭州云栖", "华东", "13800000001"],
                        ["深圳前海", "华南", "13800000002"],
                        ["北京中关村", "华北", "13800000003"],
                    ],
                ),
                (
                    "订单",
                    [
                        ["订单编号", "客户名称", "金额", "状态", "备注"],
                        ["SO-1", "杭州云栖", 199, "待付款", 1],
                        ["SO-2", "深圳前海", 99, "已完成", 2],
                        ["SO-4", "北京中关村", 59, "待付款", 3],
                        ["SO-5", "杭州云栖", 29, "已完成", 4],
                    ],
                ),
            ]
        ),
    )


def _file_b() -> tuple[str, bytes]:
    return (
        "华南销售.xlsx",
        _workbook(
            [
                (
                    "订单",
                    [
                        ["订单编号", "客户名称", "金额", "状态", "备注"],
                        ["SO-2", "深圳前海", 99, "已完成", "加急"],
                        ["SO-3", "北京中关村", 59, "已取消", "普通"],
                        ["SO-6", "杭州云栖", 39, "已取消", "加急"],
                        ["SO-7", "深圳前海", 79, "已完成", "普通"],
                    ],
                )
            ]
        ),
    )


def _parse(files: list[tuple[str, bytes]]) -> dict:
    response = client.post(
        "/api/v1/ingestion/parse-multi",
        files=[("files", (name, content, XLSX)) for name, content in files],
    )
    assert response.status_code == 200, response.text
    return response.json()


def _acknowledged(result: dict) -> list[str]:
    model = result["business_model"]
    sheet_by_name = {sheet["name"]: sheet for sheet in result["sheets"]}
    ids: list[str] = []
    for entity in model["entities"]:
        sheet_name = (entity.get("source") or {}).get("sheet")
        inferred = sheet_by_name.get(sheet_name, {}).get("inferred_entity")
        if inferred and inferred["needs_review"]:
            ids.append(entity["key"])
        for field in entity["fields"]:
            if field["needs_review"]:
                ids.append(f"{entity['key']}.{field['key']}")
    for metric in model.get("metrics", []):
        if metric["needs_review"]:
            ids.append(f"metric:{metric['key']}")
    return ids


def _links(result: dict) -> dict[str, dict[str, str]]:
    return {
        link["key"]: {
            "decision": "accepted",
            "on_from": link["on_from"],
            "on_to": link["on_to"],
        }
        for link in result["inferred_links"]
    }


def _conflict_kinds(result: dict) -> set[str]:
    return {conflict["kind"] for conflict in result["conflicts"]}


def test_parse_multi_merges_same_entity_across_files():
    result = _parse([_file_a(), _file_b()])

    assert [item["name"] for item in result["files"]] == [
        "华北销售.xlsx",
        "华南销售.xlsx",
    ]
    assert {sheet["file_name"] for sheet in result["sheets"]} == {
        "华北销售.xlsx",
        "华南销售.xlsx",
    }
    assert len(result["sheets"]) == 3

    order_sheets = [
        sheet
        for sheet in result["sheets"]
        if sheet["inferred_entity"]
        and sheet["inferred_entity"]["key"] == "order"
    ]
    assert len(order_sheets) == 2
    merge = next(item for item in result["merges"] if item["entity_key"] == "order")
    assert set(merge["sheet_ids"]) == {sheet["id"] for sheet in order_sheets}
    assert merge["row_count"] == 8

    assert _conflict_kinds(result) == {
        "primary_key_duplicate",
        "field_type_mismatch",
        "enum_values_expanded",
    }
    assert {entity["key"] for entity in result["business_model"]["entities"]} == {
        "customer",
        "order",
    }


def test_preview_splitting_a_merge_group_creates_separate_entities():
    result = _parse([_file_a(), _file_b()])
    sheet_ids = [sheet["id"] for sheet in result["sheets"]]
    preview = client.post(
        "/api/v1/ingestion/parse-multi/preview",
        files=[
            ("files", ("华北销售.xlsx", _file_a()[1], XLSX)),
            ("files", ("华南销售.xlsx", _file_b()[1], XLSX)),
        ],
        data={"options": json.dumps({"sheets": sheet_ids, "groups": []})},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["merges"] == []
    assert len(body["business_model"]["entities"]) == 3
    assert len(body["inferred_links"]) >= len(result["inferred_links"])


def test_confirm_multi_applies_merge_and_duplicate_key_strategy():
    result = _parse([_file_a(), _file_b()])
    merge = next(item for item in result["merges"] if item["entity_key"] == "order")
    decisions = {
        "sheets": [sheet["id"] for sheet in result["sheets"]],
        "groups": [{"sheets": merge["sheet_ids"]}],
        "conflicts": {
            conflict["id"]: (
                "skip"
                if conflict["kind"] == "primary_key_duplicate"
                else conflict["default"]
            )
            for conflict in result["conflicts"]
        },
        "acknowledged": _acknowledged(result),
        "links": _links(result),
    }
    response = client.post(
        "/api/v1/ingestion/confirm-multi",
        files=[
            ("files", ("华北销售.xlsx", _file_a()[1], XLSX)),
            ("files", ("华南销售.xlsx", _file_b()[1], XLSX)),
        ],
        data={"decisions": json.dumps(decisions)},
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["app_key"] == "workspace"
    assert summary["record_counts"]["customer"] == 3
    # SO-2 appears in both files; "skip" keeps the first occurrence.
    assert summary["record_counts"]["order"] == 7

    model = client.get("/api/v1/runtime/workspace/bootstrap").json()["model"]
    order = next(entity for entity in model["entities"] if entity["key"] == "order")
    orders = rows(client, "workspace", "order")
    assert {row[order["key_field"]] for row in orders} == {
        "SO-1",
        "SO-2",
        "SO-3",
        "SO-4",
        "SO-5",
        "SO-6",
        "SO-7",
    }

    note = next(field for field in order["fields"] if field["name"] == "备注")
    # Type mismatch resolved to plain text so both numbers and words fit.
    assert note["type"] == "string"


def test_confirm_multi_creates_system_under_requested_app_key():
    result = _parse([_file_a(), _file_b()])
    merge = next(item for item in result["merges"] if item["entity_key"] == "order")
    decisions = {
        "sheets": [sheet["id"] for sheet in result["sheets"]],
        "groups": [{"sheets": merge["sheet_ids"]}],
        "conflicts": {
            conflict["id"]: (
                "skip"
                if conflict["kind"] == "primary_key_duplicate"
                else conflict["default"]
            )
            for conflict in result["conflicts"]
        },
        "acknowledged": _acknowledged(result),
        "links": _links(result),
    }
    response = client.post(
        "/api/v1/ingestion/confirm-multi",
        files=[
            ("files", ("华北销售.xlsx", _file_a()[1], XLSX)),
            ("files", ("华南销售.xlsx", _file_b()[1], XLSX)),
        ],
        data={"decisions": json.dumps(decisions), "app_key": "sales-2026"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["app_key"] == "sales-2026"
    assert client.get("/api/v1/runtime/sales-2026/bootstrap").status_code == 200
    assert client.get("/api/v1/runtime/workspace/bootstrap").status_code == 404

    repeat = client.post(
        "/api/v1/ingestion/confirm-multi",
        files=[
            ("files", ("华北销售.xlsx", _file_a()[1], XLSX)),
            ("files", ("华南销售.xlsx", _file_b()[1], XLSX)),
        ],
        data={"decisions": json.dumps(decisions), "app_key": "sales-2026"},
    )
    assert repeat.status_code == 409, repeat.text


def test_confirm_multi_keeping_duplicates_suffixes_the_key():
    result = _parse([_file_a(), _file_b()])
    merge = next(item for item in result["merges"] if item["entity_key"] == "order")
    decisions = {
        "sheets": [sheet["id"] for sheet in result["sheets"]],
        "groups": [{"sheets": merge["sheet_ids"]}],
        "conflicts": {"pk:order": "keep"},
        "acknowledged": _acknowledged(result),
        "links": _links(result),
    }
    response = client.post(
        "/api/v1/ingestion/confirm-multi",
        files=[
            ("files", ("华北销售.xlsx", _file_a()[1], XLSX)),
            ("files", ("华南销售.xlsx", _file_b()[1], XLSX)),
        ],
        data={"decisions": json.dumps(decisions)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["record_counts"]["order"] == 8
    model = client.get("/api/v1/runtime/workspace/bootstrap").json()["model"]
    order = next(entity for entity in model["entities"] if entity["key"] == "order")
    order_rows = rows(client, "workspace", "order")
    assert {row[order["key_field"]] for row in order_rows} == {
        "SO-1",
        "SO-2",
        "SO-2-2",
        "SO-3",
        "SO-4",
        "SO-5",
        "SO-6",
        "SO-7",
    }


def _three_files() -> list[tuple[str, bytes]]:
    """3 files / 6 sheets: customer + order + product, each spread over two."""
    customer: list[list[object]] = [
        ["客户名称", "区域", "联系电话"],
        ["杭州云栖", "华东", "13800000001"],
        ["深圳前海", "华南", "13800000002"],
        ["北京中关村", "华北", "13800000003"],
        ["成都天府", "西南", "13800000004"],
    ]
    orders: list[list[object]] = [
        ["订单编号", "客户名称", "金额", "状态", "下单日期"],
        ["SO-1", "杭州云栖", 199, "待付款", "2025-01-03"],
        ["SO-2", "深圳前海", 99, "已完成", "2025-01-04"],
    ]
    products: list[list[object]] = [
        ["商品编号", "商品名称", "单价", "库存"],
        ["P-1", "云栖套餐", 199, 12],
        ["P-2", "前海配件", 39, 40],
    ]
    return [
        ("华北.xlsx", _workbook([("客户", customer), ("订单", orders)])),
        ("华南.xlsx", _workbook([("订单", orders), ("商品", products)])),
        ("西北.xlsx", _workbook([("客户", customer), ("商品", products)])),
    ]


def test_three_files_six_sheets_generate_a_valid_model():
    files = _three_files()
    result = _parse(files)
    assert len(result["files"]) == 3
    assert len(result["sheets"]) == 6
    assert {entity["key"] for entity in result["business_model"]["entities"]} == {
        "customer",
        "order",
        "product",
    }
    # Each kind is spread over two sheets, so every entity carries one merge.
    assert {merge["entity_key"] for merge in result["merges"]} == {
        "customer",
        "order",
        "product",
    }

    decisions = {
        "sheets": [sheet["id"] for sheet in result["sheets"]],
        "groups": [{"sheets": merge["sheet_ids"]} for merge in result["merges"]],
        "conflicts": {
            conflict["id"]: conflict["default"] for conflict in result["conflicts"]
        },
        "acknowledged": _acknowledged(result),
        "links": _links(result),
    }
    response = client.post(
        "/api/v1/ingestion/confirm-multi",
        files=[("files", (name, content, XLSX)) for name, content in files],
        data={"decisions": json.dumps(decisions)},
    )
    assert response.status_code == 200, response.text
    counts = response.json()["record_counts"]
    # The same rows appear in both files; the default skip strategy keeps one.
    assert counts["customer"] == 4
    assert counts["order"] == 2
    assert counts["product"] == 2


def test_parse_multi_rejects_unknown_sheet_and_missing_files():
    response = client.post(
        "/api/v1/ingestion/parse-multi",
        files=[("files", ("a.xlsx", b"not an excel", XLSX))],
    )
    assert response.status_code == 422

    empty = client.post("/api/v1/ingestion/parse-multi", files=[])
    assert empty.status_code in {400, 422}

    result = _parse([_file_a()])
    preview = client.post(
        "/api/v1/ingestion/parse-multi/preview",
        files=[("files", ("华北销售.xlsx", _file_a()[1], XLSX))],
        data={"options": json.dumps({"sheets": ["s99"], "groups": []})},
    )
    assert preview.status_code == 422
    assert "s99" in preview.json()["detail"]
    assert result["sheets"]
