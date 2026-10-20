"""Append data to an existing entity: preview, duplicate strategies, rollback."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.api.main import app
from tests.runtime_helpers import rows
from tests.test_multi_import import _acknowledged, _links, _parse, _workbook

client = TestClient(app)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _system_file() -> tuple[str, bytes]:
    return (
        "销售台账.xlsx",
        _workbook(
            [
                (
                    "客户",
                    [
                        ["客户编号", "客户名称", "区域", "联系电话"],
                        ["C001", "杭州云栖", "华东", "13800000001"],
                        ["C002", "深圳前海", "华南", "13800000002"],
                    ],
                ),
                (
                    "订单",
                    [
                        ["订单编号", "客户名称", "金额", "状态", "备注"],
                        ["SO-1", "杭州云栖", 199, "待付款", "首单"],
                        ["SO-2", "深圳前海", 99, "已完成", "普通"],
                    ],
                ),
            ]
        ),
    )


def _append_file(extra_column: bool = False, amount: int = 59) -> tuple[str, bytes]:
    header: list[object] = ["订单编号", "客户名称", "金额", "状态", "备注"]
    first: list[object] = ["SO-8", "杭州云栖", amount, "待付款", "追加"]
    second: list[object] = ["SO-2", "深圳前海", amount, "已完成", "重复"]
    if extra_column:
        header.append("业务员")
        first.append("张三")
        second.append("李四")
    return "追加订单.xlsx", _workbook([("订单", [header, first, second])])


def _create_system() -> None:
    name, content = _system_file()
    result = _parse([(name, content)])
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
        files=[("files", (name, content, XLSX))],
        data={"decisions": json.dumps(decisions)},
    )
    assert response.status_code == 200, response.text


def _preview(entity_key: str = "order", **data: str) -> dict:
    name, content = _append_file()
    response = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": (name, content, XLSX)},
        data={"entity_key": entity_key, "app_key": "workspace", **data},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _append_rows() -> list[dict]:
    return rows(client, "workspace", "order")


def test_append_preview_reports_duplicates_and_new_fields():
    _create_system()
    name, content = _append_file(extra_column=True)
    response = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": (name, content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    )
    assert response.status_code == 200, response.text
    preview = response.json()

    assert preview["entity_key"] == "order"
    assert preview["base_version"] == 1
    assert preview["valid_rows"] == 2
    assert preview["duplicate_rows"] == 1
    assert preview["duplicate_sample"] == ["SO-2"]
    assert preview["new_rows"] == 1
    assert [field["name"] for field in preview["new_fields"]] == ["业务员"]
    assert {field["key"] for field in preview["matched_fields"]} >= {
        "order_no",
        "amount",
    }
    assert [option["value"] for option in preview["strategies"]] == [
        "skip",
        "overwrite",
        "keep",
    ]


def test_append_skip_adds_new_rows_and_advances_model_for_new_field():
    _create_system()
    name, content = _append_file(extra_column=True)
    response = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": (name, content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["new_fields"]

    response = client.post(
        "/api/v1/ingestion/appends/confirm",
        json={"batch_id": preview["batch_id"], "strategy": "skip"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["inserted"] == 1
    assert result["skipped"] == 1
    assert result["overwritten"] == 0
    assert result["version"] == preview["base_version"] + 1

    bootstrap = client.get("/api/v1/runtime/workspace/bootstrap").json()
    assert bootstrap["version"] == preview["base_version"] + 1
    order = next(item for item in bootstrap["model"]["entities"] if item["key"] == "order")
    assert "业务员" in {field["name"] for field in order["fields"]}

    keys = {row[order["key_field"]] for row in _append_rows()}
    assert keys == {"SO-1", "SO-2", "SO-8"}
    appended = next(row for row in _append_rows() if row["order_no"] == "SO-8")
    new_field_key = next(
        field["key"] for field in order["fields"] if field["name"] == "业务员"
    )
    assert appended[new_field_key] == "张三"

    batches = client.get(
        "/api/v1/ingestion/appends", params={"app_key": "workspace"}
    ).json()
    assert batches[0]["status"] == "applied"
    assert batches[0]["inserted"] == 1


def test_append_rollback_removes_rows_and_new_field():
    _create_system()
    name, content = _append_file(extra_column=True)
    preview = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": (name, content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    ).json()
    base = preview["base_version"]

    applied = client.post(
        "/api/v1/ingestion/appends/confirm",
        json={"batch_id": preview["batch_id"], "strategy": "skip"},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["version"] == base + 1

    response = client.post(
        f"/api/v1/ingestion/appends/{preview['batch_id']}/rollback", json={}
    )
    assert response.status_code == 200, response.text
    rolled = response.json()
    assert rolled["status"] == "rolled_back"
    # Restoring the pre-append snapshot is itself an appended version.
    assert rolled["version"] == base + 2

    bootstrap = client.get("/api/v1/runtime/workspace/bootstrap").json()
    order = next(item for item in bootstrap["model"]["entities"] if item["key"] == "order")
    assert "业务员" not in {field["name"] for field in order["fields"]}
    assert {row[order["key_field"]] for row in _append_rows()} == {"SO-1", "SO-2"}


def test_append_overwrite_replaces_row_and_rollback_restores_it():
    _create_system()
    name, content = _append_file(amount=123)
    preview = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": (name, content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    ).json()
    assert preview["new_fields"] == []

    applied = client.post(
        "/api/v1/ingestion/appends/confirm",
        json={"batch_id": preview["batch_id"], "strategy": "overwrite"},
    )
    assert applied.status_code == 200, applied.text
    summary = applied.json()
    assert summary["overwritten"] == 1
    assert summary["inserted"] == 1
    # No new field: the model version must not advance.
    assert summary["version"] == preview["base_version"]

    order_row = next(row for row in _append_rows() if row["order_no"] == "SO-2")
    assert order_row["amount"] == 123

    response = client.post(
        f"/api/v1/ingestion/appends/{preview['batch_id']}/rollback", json={}
    )
    assert response.status_code == 200, response.text
    restored = next(row for row in _append_rows() if row["order_no"] == "SO-2")
    assert restored["amount"] == 99
    assert {row["order_no"] for row in _append_rows()} == {"SO-1", "SO-2"}


def test_append_keep_suffixes_the_duplicate_business_key():
    _create_system()
    name, content = _append_file()
    preview = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": (name, content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    ).json()

    response = client.post(
        "/api/v1/ingestion/appends/confirm",
        json={"batch_id": preview["batch_id"], "strategy": "keep"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["inserted"] == 2

    assert {row["order_no"] for row in _append_rows()} == {
        "SO-1",
        "SO-2",
        "SO-2-2",
        "SO-8",
    }


def test_two_consecutive_appends_advance_the_version_chain():
    _create_system()
    first_name, first_content = _append_file(extra_column=True)
    first = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": (first_name, first_content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    ).json()
    first_result = client.post(
        "/api/v1/ingestion/appends/confirm",
        json={"batch_id": first["batch_id"], "strategy": "skip"},
    ).json()
    assert first_result["version"] == first["base_version"] + 1

    second_content = _workbook(
        [
            (
                "订单",
                [
                    ["订单编号", "客户名称", "金额", "负责人"],
                    ["SO-9", "杭州云栖", 29, "王五"],
                ],
            )
        ]
    )
    second = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": ("第二次追加.xlsx", second_content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    ).json()
    assert second["base_version"] == first_result["version"]
    assert [field["name"] for field in second["new_fields"]] == ["负责人"]

    second_result = client.post(
        "/api/v1/ingestion/appends/confirm",
        json={"batch_id": second["batch_id"], "strategy": "skip"},
    ).json()
    assert second_result["version"] == second["base_version"] + 1

    bootstrap = client.get("/api/v1/runtime/workspace/bootstrap").json()
    order = next(item for item in bootstrap["model"]["entities"] if item["key"] == "order")
    names = {field["name"] for field in order["fields"]}
    assert {"业务员", "负责人"} <= names

    row = next(item for item in _append_rows() if item["order_no"] == "SO-9")
    responsible = next(field for field in order["fields"] if field["name"] == "负责人")
    assert row[responsible["key"]] == "王五"

    batches = client.get(
        "/api/v1/ingestion/appends", params={"app_key": "workspace"}
    ).json()
    assert [batch["status"] for batch in batches[:2]] == ["applied", "applied"]


def test_append_requires_the_primary_key_column():
    _create_system()
    content = _workbook([("订单", [["客户名称", "金额"], ["杭州云栖", 59]])])
    response = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": ("缺主键.xlsx", content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    )
    assert response.status_code == 422, response.text
    assert "主键列" in response.json()["detail"]


def test_append_confirm_rejects_a_stale_batch():
    _create_system()
    name, content = _append_file()
    preview = client.post(
        "/api/v1/ingestion/appends/preview",
        files={"file": (name, content, XLSX)},
        data={"entity_key": "order", "app_key": "workspace"},
    ).json()

    # A model change moves the app past the preview's base version.
    model = client.get("/api/v1/runtime/workspace/bootstrap").json()["model"]
    entity_index = next(
        index
        for index, entity in enumerate(model["entities"])
        if entity["key"] == "order"
    )
    patch = client.post(
        "/api/v1/models/workspace/patches",
        json={
            "ops": [
                {
                    "op": "add",
                    "path": f"/entities/{entity_index}/fields/-",
                    "value": {
                        "key": "append_probe",
                        "name": "追加测试字段",
                        "type": "string",
                        "role": "text",
                        "confidence": 0.9,
                    },
                }
            ],
            "source_request": "测试",
            "operator": "test",
        },
    )
    assert patch.status_code == 201, patch.text

    response = client.post(
        "/api/v1/ingestion/appends/confirm",
        json={"batch_id": preview["batch_id"], "strategy": "skip"},
    )
    assert response.status_code == 409, response.text
