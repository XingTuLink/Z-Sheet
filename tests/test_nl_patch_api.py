"""End-to-end API tests for Day 25 NL Patch preview/confirm/reject.

The LLM is replaced by a dependency override returning canned intent JSON.
Coverage: happy path with real version persistence, high-risk confirm gating,
reject/confirm idempotency, tampered/unknown tokens, cross-app isolation,
base-version conflicts, expiry, unsupported-only sentences, generator safety
refusals and LLM outages.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.ai.provider import LLMConfigError
from backend.api.main import app
from backend.api.routes.patch import get_chat_client
from backend.storage.db import SessionLocal
from backend.storage.models import PatchSessionRecord
from backend.storage.repositories import patch_session_repository

client = TestClient(app)
SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")

FIELD_ADD_RESPONSE: dict[str, Any] = {
    "intents": [
        {
            "kind": "field_add",
            "entity": "客户",
            "params": {"name": "微信号", "type": "string"},
            "confidence": 0.95,
            "reason": "用户想在客户里记录微信",
        }
    ]
}
FIELD_REMOVE_RESPONSE: dict[str, Any] = {
    "intents": [
        {
            "kind": "field_remove",
            "entity": "客户",
            "field": "联系电话",
            "confidence": 0.9,
        }
    ]
}
IDENTIFIER_REMOVE_RESPONSE: dict[str, Any] = {
    "intents": [
        {
            "kind": "field_remove",
            "entity": "客户",
            "field": "客户名称",
            "confidence": 0.9,
        }
    ]
}
UNSUPPORTED_RESPONSE: dict[str, Any] = {
    "intents": [
        {
            "kind": "unsupported",
            "category": "data_edit",
            "reason": "这是修改业务数据，不是修改系统结构",
            "suggestion": "请直接在列表页编辑该行数据",
        }
    ]
}


class FakeChatClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[Any] = []

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append(messages)
        return self.response


class ExplodingChatClient:
    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        raise LLMConfigError("未配置 LLM API Key（ZSHEET_LLM_API_KEY）")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _import(app_key: str = "default") -> None:
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": SALES_YAML},
    )
    assert response.status_code == 201, response.text


def _preview(
    response: dict[str, Any], *, app_key: str = "default", text: str = "帮我加个字段"
):
    fake = FakeChatClient(response)
    app.dependency_overrides[get_chat_client] = lambda: fake
    return client.post(
        f"/api/v1/models/{app_key}/patch-sessions",
        json={"text": text},
    )


def _confirm(app_key: str, session_id: str):
    return client.post(
        f"/api/v1/models/{app_key}/patch-sessions/{session_id}/confirm"
    )


def _reject(app_key: str, session_id: str):
    return client.post(
        f"/api/v1/models/{app_key}/patch-sessions/{session_id}/reject"
    )


def test_preview_returns_grounded_changes_without_persisting():
    _import()
    response = _preview(FIELD_ADD_RESPONSE, text="客户里加一个微信号字段")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["base_version"] == 1
    assert body["actionable"] is True
    assert body["requires_confirmation"] is False
    assert body["unsupported"] == []
    assert len(body["changes"]) == 1
    change = body["changes"][0]
    assert change["kind"] == "field_add"
    assert change["risk"] == "low"
    assert "微信号" in change["summary"]
    assert len(body["session_id"]) >= 20

    # Preview alone must not create a model version.
    versions = client.get("/api/v1/models/default/versions").json()
    assert [v["version"] for v in versions] == [1]


def test_confirm_applies_cached_ops_as_new_version():
    _import()
    session_id = _preview(FIELD_ADD_RESPONSE, text="客户里加一个微信号字段").json()[
        "session_id"
    ]

    applied = _confirm("default", session_id)
    assert applied.status_code == 200, applied.text
    version = applied.json()
    assert version["version"] == 2
    assert version["has_patch"] is True
    assert version["source_request"] == "客户里加一个微信号字段"

    customer = next(
        entity
        for entity in version["snapshot"]["entities"]
        if entity["key"] == "customer"
    )
    assert any(field["name"] == "微信号" for field in customer["fields"])

    current = client.get("/api/v1/models/default").json()
    assert current["version"] == 2


def test_high_risk_change_is_flagged_but_still_applies_on_confirm():
    _import()
    body = _preview(FIELD_REMOVE_RESPONSE, text="把联系电话删掉").json()
    assert body["requires_confirmation"] is True
    assert body["changes"][0]["risk"] == "high"
    assert any("高风险" in note for note in body["notes"])

    applied = _confirm("default", body["session_id"])
    assert applied.status_code == 200
    customer = next(
        entity
        for entity in applied.json()["snapshot"]["entities"]
        if entity["key"] == "customer"
    )
    assert all(field["key"] != "phone" for field in customer["fields"])


def test_reject_keeps_model_and_blocks_later_confirm():
    _import()
    body = _preview(FIELD_REMOVE_RESPONSE).json()

    rejected = _reject("default", body["session_id"])
    assert rejected.status_code == 200
    assert rejected.json() == {"status": "rejected"}

    again = _confirm("default", body["session_id"])
    assert again.status_code == 409
    assert client.get("/api/v1/models/default").json()["version"] == 1


def test_confirm_is_single_use(caplog: pytest.LogCaptureFixture):
    caplog.set_level("INFO", logger="zsheet")
    _import()
    session_id = _preview(FIELD_ADD_RESPONSE).json()["session_id"]
    assert _confirm("default", session_id).status_code == 200
    second = _confirm("default", session_id)
    assert second.status_code == 409
    assert client.get("/api/v1/models/default").json()["version"] == 2
    # A duplicate confirm must leave a trace so operators can spot double-click
    # or retry storms rather than only seeing a bare 409.
    assert any("补丁操作被拒" in record.getMessage() for record in caplog.records)


def test_begin_confirm_claims_a_pending_preview_exactly_once():
    """Concurrent-confirm guard: the status flip is an atomic compare-and-swap.

    Two confirms racing on the same preview must not both proceed. Only the
    first CAS wins; the loser sees False (the route turns that into 409). The
    flip rides in the caller's transaction, so rolling back leaves it pending.
    """
    sessions = patch_session_repository

    _import()
    session_id = _preview(FIELD_ADD_RESPONSE).json()["session_id"]

    db = SessionLocal()
    try:
        record = sessions.get_session(db, session_id, "default")
        assert record is not None
        assert sessions.begin_confirm(db, record) is True
        assert sessions.begin_confirm(db, record) is False
        db.rollback()  # uncommitted loss: the preview must return to pending
    finally:
        db.close()

    # Still pending, so a real confirm succeeds and appends version 2.
    assert _confirm("default", session_id).status_code == 200
    assert client.get("/api/v1/models/default").json()["version"] == 2


def test_unknown_and_tampered_session_tokens_are_404():
    _import()
    assert _confirm("default", "not-a-real-token").status_code == 404
    assert _reject("default", "not-a-real-token").status_code == 404


def test_session_token_is_scoped_to_its_app():
    _import("default")
    _import("other")
    session_id = _preview(FIELD_ADD_RESPONSE, app_key="default").json()["session_id"]

    # The token must not work against another app's namespace.
    assert _confirm("other", session_id).status_code == 404
    assert _confirm("default", session_id).status_code == 200


def test_confirm_refuses_when_model_changed_after_preview():
    _import()
    session_id = _preview(FIELD_ADD_RESPONSE).json()["session_id"]

    # Some other change lands first (raw Day 3 patch endpoint).
    bumped = client.post(
        "/api/v1/models/default/patches",
        json={"ops": [{"op": "replace", "path": "/app/name", "value": "新名字"}]},
    )
    assert bumped.status_code == 201, bumped.text

    conflict = _confirm("default", session_id)
    assert conflict.status_code == 409
    assert "重新生成" in conflict.json()["detail"]


def test_expired_preview_is_refused():
    _import()
    session_id = _preview(FIELD_ADD_RESPONSE).json()["session_id"]

    db = SessionLocal()
    try:
        record = db.get(PatchSessionRecord, session_id)
        assert record is not None
        record.created_at = datetime.now(UTC) - timedelta(hours=2)
        db.commit()
    finally:
        db.close()

    response = _confirm("default", session_id)
    assert response.status_code == 409
    assert "过期" in response.json()["detail"]


def test_unsupported_only_sentence_is_not_actionable():
    _import()
    body = _preview(UNSUPPORTED_RESPONSE, text="帮我把张三那条订单删掉").json()
    assert body["actionable"] is False
    assert body["changes"] == []
    assert len(body["unsupported"]) == 1
    item = body["unsupported"][0]
    assert item["category"] == "data_edit"
    assert item["suggestion"]

    # Nothing to apply: even a forced confirm must not create a version.
    assert _confirm("default", body["session_id"]).status_code == 422
    assert client.get("/api/v1/models/default").json()["version"] == 1


def test_generator_safety_refusal_returns_chinese_422():
    _import()
    response = _preview(IDENTIFIER_REMOVE_RESPONSE, text="把客户名称字段删了")
    assert response.status_code == 422
    assert response.json()["detail"]  # Chinese refusal, never a raw 500


def test_llm_outage_is_surfaced_as_503():
    _import()
    app.dependency_overrides[get_chat_client] = lambda: ExplodingChatClient()
    response = client.post(
        "/api/v1/models/default/patch-sessions",
        json={"text": "随便改点什么"},
    )
    assert response.status_code == 503
    assert "LLM" in response.json()["detail"]


def test_preview_unknown_app_is_404():
    response = _preview(FIELD_ADD_RESPONSE, app_key="ghost")
    assert response.status_code == 404


def test_empty_text_is_rejected_by_schema():
    _import()
    app.dependency_overrides[get_chat_client] = lambda: FakeChatClient(
        FIELD_ADD_RESPONSE
    )
    response = client.post(
        "/api/v1/models/default/patch-sessions", json={"text": "   "}
    )
    assert response.status_code == 422
