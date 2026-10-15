"""Day 18 tests for row create/update/delete persistence."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


def bootstrap_rows(entity: str) -> list[dict]:
    response = client.get("/api/v1/runtime/default/bootstrap")
    assert response.status_code == 200
    return response.json()["records"][entity]


def customer(name: str, **overrides: object) -> dict[str, object]:
    return {
        "customer_name": name,
        "region": "华东",
        "owner": "测试负责人",
        "phone": "13800000000",
        **overrides,
    }


def test_create_row_persists_to_following_bootstrap() -> None:
    before = len(bootstrap_rows("customer"))
    payload = customer("测试新增客户")

    response = client.post(
        "/api/v1/runtime/default/entities/customer/records",
        json={"row": payload},
    )

    assert response.status_code == 201
    assert response.json()["row"] == payload
    rows = bootstrap_rows("customer")
    assert len(rows) == before + 1
    assert any(row["customer_name"] == "测试新增客户" for row in rows)


def test_duplicate_key_is_conflict() -> None:
    payload = customer("重复客户")
    first = client.post(
        "/api/v1/runtime/default/entities/customer/records",
        json={"row": payload},
    )
    assert first.status_code == 201

    response = client.post(
        "/api/v1/runtime/default/entities/customer/records",
        json={"row": payload},
    )
    assert response.status_code == 409
    assert "已存在" in response.json()["detail"]


def test_create_validates_required_key_enum_phone_and_date() -> None:
    cases = [
        ({**customer("空标识"), "customer_name": ""}, 422),
        ({**customer("错误区域"), "region": "火星"}, 422),
        ({**customer("错误电话"), "phone": 123}, 422),
        (
            {
                "order_no": "BAD-DATE",
                "customer_name": "日期客户",
                "amount": 100,
                "order_date": "2026/01/01",
            },
            422,
        ),
        (
            {
                "order_no": "BAD-NUMBER",
                "customer_name": "金额客户",
                "amount": True,
                "order_date": "2026-01-01",
            },
            422,
        ),
    ]
    for row, expected in cases:
        entity = "order" if "order_no" in row else "customer"
        response = client.post(
            f"/api/v1/runtime/default/entities/{entity}/records",
            json={"row": row},
        )
        assert response.status_code == expected, row


def test_create_rejects_unknown_field() -> None:
    response = client.post(
        "/api/v1/runtime/default/entities/customer/records",
        json={"row": {**customer("未知字段客户"), "unexpected": "x"}},
    )
    assert response.status_code == 422
    assert "不存在" in response.json()["detail"]


def test_update_row_persists_and_cannot_change_key() -> None:
    created = client.post(
        "/api/v1/runtime/default/entities/customer/records",
        json={"row": customer("待编辑客户", owner="旧负责人")},
    )
    assert created.status_code == 201

    changed = customer("待编辑客户", owner="新负责人")
    response = client.put(
        "/api/v1/runtime/default/entities/customer/records/待编辑客户",
        json={"row": changed},
    )
    assert response.status_code == 200
    persisted = next(
        row
        for row in bootstrap_rows("customer")
        if row["customer_name"] == "待编辑客户"
    )
    assert persisted["owner"] == "新负责人"

    changed_key = customer("被改名客户", owner="新负责人")
    forbidden = client.put(
        "/api/v1/runtime/default/entities/customer/records/待编辑客户",
        json={"row": changed_key},
    )
    assert forbidden.status_code == 422


def test_update_missing_row_is_404() -> None:
    response = client.put(
        "/api/v1/runtime/default/entities/customer/records/不存在客户",
        json={"row": customer("不存在客户")},
    )
    assert response.status_code == 404


def test_delete_row_removes_it_from_bootstrap() -> None:
    payload = customer("待删除客户")
    created = client.post(
        "/api/v1/runtime/default/entities/customer/records",
        json={"row": payload},
    )
    assert created.status_code == 201

    response = client.delete(
        "/api/v1/runtime/default/entities/customer/records/待删除客户"
    )
    assert response.status_code == 204
    assert all(
        row["customer_name"] != "待删除客户"
        for row in bootstrap_rows("customer")
    )
    repeat = client.delete(
        "/api/v1/runtime/default/entities/customer/records/待删除客户"
    )
    assert repeat.status_code == 404


def test_unknown_app_and_entity_are_not_found() -> None:
    app_response = client.post(
        "/api/v1/runtime/missing/entities/customer/records",
        json={"row": customer("无应用客户")},
    )
    assert app_response.status_code == 404

    entity_response = client.post(
        "/api/v1/runtime/default/entities/missing/records",
        json={"row": {"missing_key": "x"}},
    )
    assert entity_response.status_code == 404
