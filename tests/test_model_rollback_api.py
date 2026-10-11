"""End-to-end API tests for Day 26 undo / rollback.

Rollback is append-only: restoring a historical snapshot appends a new
version row, history is never rewritten, and the version chain keeps
accepting patches afterwards.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)
SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")


def _import(app_key: str = "rbapp") -> None:
    r = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": SALES_YAML},
    )
    assert r.status_code == 201, r.text


def _rename(app_key: str, new_name: str) -> None:
    r = client.post(
        f"/api/v1/models/{app_key}/patches",
        json={
            "ops": [{"op": "replace", "path": "/app/name", "value": new_name}],
            "source_request": f"改名为{new_name}",
        },
    )
    assert r.status_code == 201, r.text


def _versions(app_key: str) -> list[dict]:
    r = client.get(f"/api/v1/models/{app_key}/versions")
    assert r.status_code == 200, r.text
    return r.json()


def _current(app_key: str) -> dict:
    r = client.get(f"/api/v1/models/{app_key}")
    assert r.status_code == 200, r.text
    return r.json()


def test_rollback_appends_identical_snapshot() -> None:
    _import()
    _rename("rbapp", "销售管理系统V2")
    _rename("rbapp", "销售管理系统V3")

    v1 = client.get("/api/v1/models/rbapp/versions/1").json()

    r = client.post("/api/v1/models/rbapp/versions/1/rollback", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 4
    assert body["snapshot"] == v1["snapshot"]
    assert body["snapshot"]["app"]["name"] == "销售管理系统"
    assert body["has_patch"] is False
    assert body["patch"] is None
    assert body["source_request"] == "回滚到版本 1"

    # History is untouched: all four versions remain, in order.
    nums = [v["version"] for v in _versions("rbapp")]
    assert nums == [1, 2, 3, 4]
    assert _current("rbapp")["version"] == 4


def test_rollback_to_missing_version_is_404() -> None:
    _import()
    r = client.post("/api/v1/models/rbapp/versions/99/rollback", json={})
    assert r.status_code == 404
    assert "99" in r.json()["detail"]


def test_rollback_to_current_version_is_409() -> None:
    _import()
    _rename("rbapp", "销售管理系统V2")
    r = client.post("/api/v1/models/rbapp/versions/2/rollback", json={})
    assert r.status_code == 409
    assert "无需回滚" in r.json()["detail"]


def test_undo_restores_previous_version() -> None:
    _import()
    _rename("rbapp", "销售管理系统V2")
    _rename("rbapp", "销售管理系统V3")

    r = client.post("/api/v1/models/rbapp/undo", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 4
    assert body["snapshot"]["app"]["name"] == "销售管理系统V2"
    assert body["source_request"] == "回滚到版本 2"
    assert body["has_patch"] is False

    # Undo walks one row back in the append-only chain, so a second undo
    # from v4 (copy of v2) restores v3 — an append-only redo toggle.
    r = client.post("/api/v1/models/rbapp/undo", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 5
    assert body["snapshot"]["app"]["name"] == "销售管理系统V3"
    assert body["source_request"] == "回滚到版本 3"


def test_undo_at_first_version_is_409() -> None:
    _import("freshapp")
    r = client.post("/api/v1/models/freshapp/undo", json={})
    assert r.status_code == 409
    assert "最早版本" in r.json()["detail"]


def test_rollback_and_undo_unknown_app_are_404() -> None:
    assert client.post("/api/v1/models/ghost/undo", json={}).status_code == 404
    assert (
        client.post("/api/v1/models/ghost/versions/1/rollback", json={}).status_code
        == 404
    )


def test_version_chain_still_accepts_patches_after_rollback() -> None:
    _import()
    _rename("rbapp", "销售管理系统V2")
    assert (
        client.post("/api/v1/models/rbapp/versions/1/rollback", json={}).status_code
        == 201
    )
    _rename("rbapp", "回滚后再改")
    body = _current("rbapp")
    assert body["version"] == 4
    assert body["snapshot"]["app"]["name"] == "回滚后再改"
    assert body["has_patch"] is True


def test_rollback_operator_is_recorded() -> None:
    _import()
    _rename("rbapp", "销售管理系统V2")
    r = client.post(
        "/api/v1/models/rbapp/versions/1/rollback",
        json={"operator": "tester"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["operator"] == "tester"


def test_rollback_preserves_intermediate_history_rows() -> None:
    _import()
    _rename("rbapp", "销售管理系统V2")
    _rename("rbapp", "销售管理系统V3")
    client.post("/api/v1/models/rbapp/versions/1/rollback", json={})

    v2 = client.get("/api/v1/models/rbapp/versions/2").json()
    v3 = client.get("/api/v1/models/rbapp/versions/3").json()
    assert v2["snapshot"]["app"]["name"] == "销售管理系统V2"
    assert v3["snapshot"]["app"]["name"] == "销售管理系统V3"
    assert v2["has_patch"] is True and v3["has_patch"] is True
