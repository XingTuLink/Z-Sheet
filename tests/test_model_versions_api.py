"""End-to-end API tests for model import/export, versioning and patches."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.domain.serialization import model_from_yaml

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
