"""Audit trail: what was recorded, who did it and how it is queried."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.storage.db import SessionLocal
from backend.storage.record_store import get_record_store

SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")

ADD_EMAIL_PATCH = {
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
    "source_request": "客户实体增加邮箱字段",
}


def _client() -> TestClient:
    return TestClient(app)


def _signup(client: TestClient, email: str = "owner@example.com") -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "测试"},
    )
    assert response.status_code == 201, response.text


def _import(client: TestClient, app_key: str) -> None:
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": SALES_YAML},
    )
    assert response.status_code == 201, response.text


def _audit(client: TestClient, app_key: str, **params) -> dict:
    response = client.get(f"/api/v1/apps/{app_key}/audit", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _actions(page: dict) -> list[str]:
    return [entry["action"] for entry in page["items"]]


def _store_customer(app_key: str, name: str, region: str) -> None:
    with SessionLocal() as db:
        store = get_record_store(db)
        store.insert_row(
            app_key,
            "customer",
            name,
            {"customer_name": name, "region": region},
            1,
            commit=False,
        )
        db.commit()


def test_import_records_creation_with_the_actor_email():
    client = _client()
    _signup(client)
    _import(client, "auditapp")

    page = _audit(client, "auditapp")
    assert page["total"] == 1
    entry = page["items"][0]
    assert entry["action"] == "app.create"
    assert entry["actor"] == "owner@example.com"
    assert entry["user_id"] is not None
    assert "销售管理系统" in entry["summary"]
    assert entry["detail"]["version"] == 1


def test_model_patch_rollback_publish_and_unpublish_are_logged():
    client = _client()
    _signup(client)
    _import(client, "auditapp")

    patched = client.post("/api/v1/models/auditapp/patches", json=ADD_EMAIL_PATCH)
    assert patched.status_code == 201, patched.text
    rolled_back = client.post("/api/v1/models/auditapp/versions/1/rollback", json={})
    assert rolled_back.status_code < 400, rolled_back.text
    published = client.post(
        "/api/v1/models/auditapp/publication", json={"slug": "audit-public"}
    )
    assert published.status_code == 201, published.text
    unpublished = client.delete("/api/v1/models/auditapp/publication")
    assert unpublished.status_code == 200, unpublished.text

    actions = _actions(_audit(client, "auditapp"))
    assert actions == [
        "app.unpublish",
        "app.publish",
        "model.rollback",
        "model.patch",
        "app.create",
    ]


def test_record_create_update_and_delete_are_logged():
    client = _client()
    _signup(client)
    _import(client, "auditapp")
    _store_customer("auditapp", "客户A", "华东")

    created = client.post(
        "/api/v1/runtime/auditapp/entities/customer/records",
        json={"row": {"customer_name": "客户B", "region": "华南"}},
    )
    assert created.status_code == 201, created.text
    updated = client.put(
        "/api/v1/runtime/auditapp/entities/customer/records/客户A",
        json={"row": {"customer_name": "客户A", "region": "华南"}},
    )
    assert updated.status_code == 200, updated.text
    deleted = client.delete(
        "/api/v1/runtime/auditapp/entities/customer/records/客户B"
    )
    assert deleted.status_code == 204, deleted.text

    actions = _actions(_audit(client, "auditapp"))
    assert actions[:3] == ["record.delete", "record.update", "record.create"]


def test_audit_is_paginated_newest_first():
    client = _client()
    _signup(client)
    _import(client, "auditapp")
    _store_customer("auditapp", "客户A", "华东")
    client.post(
        "/api/v1/runtime/auditapp/entities/customer/records",
        json={"row": {"customer_name": "客户B", "region": "华南"}},
    )
    client.put(
        "/api/v1/runtime/auditapp/entities/customer/records/客户A",
        json={"row": {"customer_name": "客户A", "region": "华南"}},
    )
    client.delete("/api/v1/runtime/auditapp/entities/customer/records/客户B")

    page = _audit(client, "auditapp", limit=2, offset=0)
    assert page["total"] == 4
    assert page["limit"] == 2
    assert len(page["items"]) == 2
    assert _actions(page) == ["record.delete", "record.update"]

    tail = _audit(client, "auditapp", limit=2, offset=2)
    assert _actions(tail) == ["record.create", "app.create"]


def test_audit_is_readable_by_members_and_hidden_from_outsiders():
    owner = _client()
    _signup(owner)
    _import(owner, "auditapp")

    viewer = _client()
    _signup(viewer, "viewer@example.com")
    added = owner.post(
        "/api/v1/apps/auditapp/members",
        json={"email": "viewer@example.com", "role": "viewer"},
    )
    assert added.status_code == 201, added.text
    assert _audit(viewer, "auditapp")["total"] >= 1

    stranger = _client()
    _signup(stranger, "stranger@example.com")
    assert stranger.get("/api/v1/apps/auditapp/audit").status_code == 403
    assert _client().get("/api/v1/apps/auditapp/audit").status_code == 401
