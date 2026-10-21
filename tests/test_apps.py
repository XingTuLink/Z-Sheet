"""Workbench system list and per-app overview endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.storage.db import SessionLocal
from backend.storage.record_store import get_record_store
from backend.storage.repositories import append_repository

client = TestClient(app)
SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")


def _import(app_key: str) -> None:
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": SALES_YAML},
    )
    assert response.status_code == 201, response.text


def _store_rows(app_key: str, entity: str, rows: list[dict[str, Any]]) -> None:
    key_field = "customer_name" if entity == "customer" else "order_no"
    with SessionLocal() as db:
        store = get_record_store(db)
        for row in rows:
            store.insert_row(app_key, entity, str(row[key_field]), row, 1, commit=False)
        db.commit()


def _apps() -> list[dict[str, Any]]:
    response = client.get("/api/v1/apps")
    assert response.status_code == 200, response.text
    return response.json()


def _overview(app_key: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/apps/{app_key}/overview")
    assert response.status_code == 200, response.text
    return response.json()


def test_apps_list_is_empty_without_systems():
    assert _apps() == []


def test_apps_list_reports_counts_names_and_version():
    _import("workspace")
    _store_rows(
        "workspace",
        "customer",
        [
            {"customer_name": "客户A", "region": "华东"},
            {"customer_name": "客户B", "region": "华南"},
        ],
    )
    _store_rows(
        "workspace",
        "order",
        [{"order_no": "SO-1", "customer_name": "客户A", "amount": 100}],
    )

    apps = _apps()
    assert len(apps) == 1
    summary = apps[0]
    assert summary["app_key"] == "workspace"
    assert summary["name"] == "销售管理系统"
    assert summary["entity_count"] == 2
    assert summary["record_count"] == 3
    assert summary["current_version"] == 1
    assert summary["publication"] is None
    assert summary["updated_at"]


def test_apps_list_keeps_multiple_systems_isolated():
    _import("workspace")
    _import("crm")
    _store_rows(
        "workspace",
        "customer",
        [{"customer_name": "客户A", "region": "华东"}],
    )

    by_key = {item["app_key"]: item for item in _apps()}
    assert set(by_key) == {"workspace", "crm"}
    assert by_key["workspace"]["record_count"] == 1
    assert by_key["crm"]["record_count"] == 0


def test_apps_list_reflects_publication_state():
    _import("workspace")
    response = client.post(
        "/api/v1/models/workspace/publication",
        json={"slug": "kehu-xiaoshou"},
    )
    assert response.status_code == 201, response.text

    publication = _apps()[0]["publication"]
    assert publication["status"] == "published"
    assert publication["slug"] == "kehu-xiaoshou"
    assert publication["public"] is True

    response = client.delete("/api/v1/models/workspace/publication")
    assert response.status_code == 200, response.text

    publication = _apps()[0]["publication"]
    assert publication["status"] == "unpublished"
    assert publication["slug"] == "kehu-xiaoshou"
    assert publication["public"] is False


def test_app_overview_reports_entities_versions_and_changes():
    _import("workspace")
    _store_rows(
        "workspace",
        "customer",
        [
            {"customer_name": "客户A", "region": "华东"},
            {"customer_name": "客户B", "region": "华南"},
        ],
    )
    _store_rows(
        "workspace",
        "order",
        [
            {"order_no": "SO-1", "customer_name": "客户A", "amount": 100},
            {"order_no": "SO-2", "customer_name": "客户B", "amount": 200},
            {"order_no": "SO-3", "customer_name": "客户A", "amount": 300},
        ],
    )

    response = client.post(
        "/api/v1/models/workspace/patches",
        json={
            "ops": [
                {
                    "op": "add",
                    "path": "/entities/0/fields/-",
                    "value": {
                        "key": "email",
                        "name": "邮箱",
                        "type": "string",
                        "role": "dimension",
                        "confidence": 0.9,
                    },
                }
            ],
            "source_request": "客户增加邮箱字段",
        },
    )
    assert response.status_code == 201, response.text

    overview = _overview("workspace")
    assert overview["app_key"] == "workspace"
    assert overview["name"] == "销售管理系统"
    assert overview["current_version"] == 2
    assert overview["version_count"] == 2
    assert overview["entity_count"] == 2
    assert overview["record_count"] == 5
    assert {
        entity["entity_key"]: entity["record_count"] for entity in overview["entities"]
    } == {"customer": 2, "order": 3}
    assert {
        entity["entity_key"]: entity["name"] for entity in overview["entities"]
    } == {"customer": "客户", "order": "订单"}

    model_events = [
        event for event in overview["recent_changes"] if event["kind"] == "model"
    ]
    assert {event["version"] for event in model_events} == {1, 2}
    latest = next(event for event in model_events if event["version"] == 2)
    assert "客户增加邮箱字段" in latest["summary"]


def test_app_overview_includes_append_events():
    _import("workspace")
    with SessionLocal() as db:
        append_repository.create(
            db,
            app_key="workspace",
            entity_key="order",
            base_version=1,
            payload={"file_name": "追加订单.xlsx", "sheet_name": "订单"},
        )

    events = _overview("workspace")["recent_changes"]
    append_events = [event for event in events if event["kind"] == "append"]
    assert len(append_events) == 1
    assert "追加订单.xlsx" in append_events[0]["summary"]
    assert "订单" in append_events[0]["summary"]
    assert append_events[0]["status"] == "pending"


def test_app_overview_unknown_system_is_404():
    response = client.get("/api/v1/apps/ghost/overview")
    assert response.status_code == 404, response.text
    assert "不存在" in response.json()["detail"]
