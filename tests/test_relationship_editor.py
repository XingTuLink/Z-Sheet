"""Visual Relationship Editor: add / update / delete through preview + confirm."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)
SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")
APP_KEY = "rel_app"


def _import() -> None:
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": APP_KEY, "yaml_content": SALES_YAML},
    )
    assert response.status_code == 201, response.text


def _preview(body: dict[str, Any]) -> Any:
    return client.post(f"/api/v1/apps/{APP_KEY}/relationships/preview", json=body)


def _confirm(session_id: str) -> Any:
    return client.post(
        f"/api/v1/models/{APP_KEY}/patch-sessions/{session_id}/confirm"
    )


def _model() -> dict[str, Any]:
    response = client.get(f"/api/v1/runtime/{APP_KEY}/bootstrap")
    assert response.status_code == 200, response.text
    return response.json()["model"]


def _detail_view(model: dict[str, Any], key: str) -> dict[str, Any]:
    return next(view for view in model["views"] if view["key"] == key)


def test_add_self_link_and_block() -> None:
    _import()
    response = _preview(
        {
            "action": "add",
            "from": "customer",
            "to": "customer",
            "type": "one_to_one",
            "on": {"from": "customer_name", "to": "owner"},
        }
    )
    assert response.status_code == 201, response.text
    session = response.json()
    assert session["requires_confirmation"] is False
    assert "自关联" in session["changes"][0]["summary"]

    confirmed = _confirm(session["session_id"])
    assert confirmed.status_code == 200, confirmed.text

    model = _model()
    link = next(item for item in model["links"] if item["key"] == "customer_self")
    assert link["type"] == "one_to_one"
    assert link["from_"] == link["to"] == "customer"
    blocks = _detail_view(model, "customer_detail")["blocks"]
    assert any(block.get("link") == "customer_self" for block in blocks)


def test_update_link_cardinality() -> None:
    _import()
    response = _preview(
        {"action": "update", "link_key": "customer_orders", "type": "one_to_one"}
    )
    assert response.status_code == 201, response.text
    assert _confirm(response.json()["session_id"]).status_code == 200

    link = next(item for item in _model()["links"] if item["key"] == "customer_orders")
    assert link["type"] == "one_to_one"


def test_update_link_matching_fields() -> None:
    _import()
    response = _preview(
        {
            "action": "update",
            "link_key": "customer_orders",
            "on": {"from": "customer_name", "to": "customer_name"},
        }
    )
    assert response.status_code == 422
    assert "无需调整" in response.json()["detail"]

    changed = _preview(
        {
            "action": "update",
            "link_key": "customer_orders",
            "on": {"from": "customer_name", "to": "order_no"},
        }
    )
    assert changed.status_code == 201, changed.text
    assert _confirm(changed.json()["session_id"]).status_code == 200
    link = next(item for item in _model()["links"] if item["key"] == "customer_orders")
    assert link["on"]["to"] == "order_no"


def test_delete_link_cascades_related_blocks() -> None:
    _import()
    response = _preview({"action": "delete", "link_key": "customer_orders"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["requires_confirmation"] is True
    assert body["changes"][0]["risk"] == "high"

    assert _confirm(body["session_id"]).status_code == 200
    model = _model()
    assert all(link["key"] != "customer_orders" for link in model["links"])
    blocks = _detail_view(model, "customer_detail")["blocks"]
    assert all(block.get("link") != "customer_orders" for block in blocks)


def test_add_rejects_self_link_on_same_field() -> None:
    _import()
    response = _preview(
        {
            "action": "add",
            "from": "customer",
            "to": "customer",
            "type": "one_to_one",
            "on": {"from": "customer_name", "to": "customer_name"},
        }
    )
    assert response.status_code == 422
    assert "不同的字段" in response.json()["detail"]


def test_add_rejects_duplicate_signature() -> None:
    _import()
    response = _preview(
        {
            "action": "add",
            "from": "customer",
            "to": "order",
            "type": "one_to_many",
            "on": {"from": "customer_name", "to": "customer_name"},
        }
    )
    assert response.status_code == 422
    assert "已存在" in response.json()["detail"]


def test_update_unknown_link_returns_422() -> None:
    _import()
    response = _preview({"action": "delete", "link_key": "ghost_link"})
    assert response.status_code == 422
    assert "关联不存在" in response.json()["detail"]


def test_relationship_edit_is_audited() -> None:
    _import()
    response = _preview({"action": "delete", "link_key": "customer_orders"})
    assert _confirm(response.json()["session_id"]).status_code == 200

    audit = client.get(f"/api/v1/apps/{APP_KEY}/audit")
    assert audit.status_code == 200, audit.text
    items = audit.json()["items"]
    assert items[0]["action"] == "model.relation"
    assert "删除关联" in items[0]["summary"]
