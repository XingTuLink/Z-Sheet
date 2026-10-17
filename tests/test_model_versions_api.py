"""End-to-end API tests for model import/export, versioning and patches."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.domain.serialization import model_from_yaml
from backend.storage.repositories import model_repository as repo

client = TestClient(app)
SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")


def _import(app_key: str = "default", yaml_text: str | None = None) -> dict:
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": yaml_text or SALES_YAML},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_import_creates_version_one():
    body = _import()
    assert body["version"] == 1
    assert body["has_patch"] is False
    assert body["patch"] is None
    assert body["validation_status"] == "passed"
    assert body["operator"] == "local"
    assert [e["key"] for e in body["snapshot"]["entities"]] == ["customer", "order"]


def test_duplicate_import_conflicts():
    _import()
    response = client.post(
        "/api/v1/models/import", json={"app_key": "default", "yaml_content": SALES_YAML}
    )
    assert response.status_code == 409


def test_malformed_yaml_is_rejected():
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": "default", "yaml_content": "app: [unterminated"},
    )
    assert response.status_code == 422
    assert "invalid YAML" in response.json()["detail"]


def test_semantically_invalid_model_is_rejected():
    response = client.post(
        "/api/v1/models/import",
        json={
            "app_key": "default",
            "yaml_content": "version: '0.1'\napp:\n  name: x\nentities: []\n",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "validation failed"
    assert response.json()["errors"]


def test_get_current_and_export_roundtrip():
    _import()
    current = client.get("/api/v1/models/default")
    assert current.status_code == 200
    assert current.json()["version"] == 1

    exported = client.get("/api/v1/models/default/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/yaml")
    reloaded = model_from_yaml(exported.text)
    assert [e.key for e in reloaded.entities] == ["customer", "order"]

    exported_json = client.get("/api/v1/models/default/export?format=json")
    assert exported_json.status_code == 200
    assert exported_json.json()["app"]["name"]


def test_unknown_app_and_version_return_404():
    assert client.get("/api/v1/models/nope").status_code == 404
    assert client.get("/api/v1/models/nope/versions").status_code == 404


def test_valid_patch_creates_version_two():
    _import()
    response = client.post(
        "/api/v1/models/default/patches",
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
                        "confidence": 0.95,
                    },
                }
            ],
            "source_request": "客户实体增加邮箱字段",
            "operator": "tester",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"] == 2
    assert body["has_patch"] is True
    assert body["patch"][0]["op"] == "add"
    assert body["source_request"] == "客户实体增加邮箱字段"
    assert body["operator"] == "tester"

    fields = [f["key"] for f in body["snapshot"]["entities"][0]["fields"]]
    assert "email" in fields

    versions = client.get("/api/v1/models/default/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["has_patch"] is False
    assert versions[1]["has_patch"] is True

    v1 = client.get("/api/v1/models/default/versions/1").json()
    assert v1["patch"] is None
    assert "email" not in [f["key"] for f in v1["snapshot"]["entities"][0]["fields"]]


def test_semantically_bad_patch_is_rejected_and_not_persisted():
    _import()
    response = client.post(
        "/api/v1/models/default/patches",
        json={
            "ops": [
                {"op": "replace", "path": "/entities/0/key_field", "value": "ghost"}
            ]
        },
    )
    assert response.status_code == 422

    # The failed change must not have created a version.
    current = client.get("/api/v1/models/default").json()
    assert current["version"] == 1
    versions = client.get("/api/v1/models/default/versions").json()
    assert len(versions) == 1


def test_structurally_bad_patch_is_rejected():
    _import()
    response = client.post(
        "/api/v1/models/default/patches",
        json={"ops": [{"op": "remove", "path": "/entities/9/fields/0"}]},
    )
    assert response.status_code == 422
    assert "could not be applied" in response.json()["detail"]


def test_patch_against_unknown_app_is_404():
    response = client.post(
        "/api/v1/models/nope/patches",
        json={"ops": [{"op": "add", "path": "/entities/-", "value": {}}]},
    )
    assert response.status_code == 404


def test_direct_patch_refuses_identity_rewrite():
    """Design 8.3 layer 3 on the raw write path: identity is never rewritten.

    The UI edits through preview/confirm; this raw endpoint has no review gate,
    so a schema-legal but business-unsafe change must be refused outright.
    """
    _import()
    response = client.post(
        "/api/v1/models/default/patches",
        json={
            "ops": [
                {"op": "replace", "path": "/entities/0/key_field", "value": "phone"}
            ]
        },
    )
    assert response.status_code == 422, response.text
    categories = {risk["category"] for risk in response.json()["risks"]}
    assert "identifier_changed" in categories
    # A refused change must not have created a version.
    assert client.get("/api/v1/models/default").json()["version"] == 1


def test_direct_patch_refuses_metric_definition_rewrite():
    _import()
    response = client.post(
        "/api/v1/models/default/patches",
        json={
            "ops": [
                {
                    "op": "replace",
                    "path": "/metrics/2/business_definition",
                    "value": "改写后的业务口径",
                }
            ]
        },
    )
    assert response.status_code == 422, response.text
    assert [risk["category"] for risk in response.json()["risks"]] == [
        "metric_definition_changed"
    ]
    assert client.get("/api/v1/models/default").json()["version"] == 1


def test_direct_patch_outcomes_are_observable(caplog: pytest.LogCaptureFixture):
    """直写 Patch 的结果必须留痕：被拦记 WARNING，成功记 INFO。"""
    _import()
    caplog.set_level("INFO", logger="zsheet")

    refused = client.post(
        "/api/v1/models/default/patches",
        json={
            "ops": [
                {"op": "replace", "path": "/entities/0/key_field", "value": "phone"}
            ]
        },
    )
    assert refused.status_code == 422, refused.text
    assert any("业务安全拦截" in record.getMessage() for record in caplog.records)

    caplog.clear()

    accepted = client.post(
        "/api/v1/models/default/patches",
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
                        "confidence": 0.95,
                    },
                }
            ]
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert any(
        "直写 Patch 生成新版本" in record.getMessage() for record in caplog.records
    )


def test_direct_patch_version_race_maps_to_conflict(monkeypatch):
    """Concurrent writes: a lost version-number race answers 409, not a 500.

    Two writers read the same current version; the ``(app_key, version)``
    unique constraint lets only one append it. Simulated by pinning
    ``get_current`` to a stale snapshot.
    """
    _import()
    add_field = {
        "op": "add",
        "path": "/entities/0/fields/-",
        "value": {
            "key": "email",
            "name": "邮箱",
            "type": "string",
            "role": "dimension",
            "confidence": 0.95,
        },
    }
    first = client.post("/api/v1/models/default/patches", json={"ops": [add_field]})
    assert first.status_code == 201, first.text

    stale = client.get("/api/v1/models/default/versions/1").json()
    monkeypatch.setattr(
        repo,
        "get_current",
        lambda _db, _app_key, **_kwargs: SimpleNamespace(
            version=1, snapshot=stale["snapshot"]
        ),
    )
    raced = client.post(
        "/api/v1/models/default/patches",
        json={"ops": [{**add_field, "value": {**add_field["value"], "key": "wechat"}}]},
    )
    assert raced.status_code == 409, raced.text
    # The losing write left no trace.
    versions = client.get("/api/v1/models/default/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
