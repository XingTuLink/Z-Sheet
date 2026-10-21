"""Global model settings: saved profiles, switching and .env fallback."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.ai.provider import (
    LLMCallError,
    LLMConfig,
    LLMProbeResult,
    probe_llm_connection,
)
from backend.api.main import app
from backend.storage.db import SessionLocal
from backend.storage.repositories import llm_repository

client = TestClient(app)

LOCAL_PROFILE = {
    "name": "本地 Ollama",
    "base_url": "http://localhost:11434/v1",
    "api_key": "",
    "model": "qwen2.5:7b",
}


def _create(**overrides: Any) -> dict[str, Any]:
    payload = {**LOCAL_PROFILE, **overrides}
    response = client.post("/api/v1/llm/profiles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _status() -> dict[str, Any]:
    response = client.get("/api/v1/llm/status")
    assert response.status_code == 200, response.text
    return response.json()


def test_status_falls_back_to_env_without_profiles():
    status = _status()
    assert status["source"] == "env"
    assert status["active_profile_id"] is None
    assert status["api_key"] == "(none)"
    assert status["available"] is False


def test_creating_a_profile_activates_it_and_masks_the_key():
    profile = _create(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key="sk-1234567890abcd",
        model="deepseek-flash",
    )
    assert profile["is_active"] is True
    assert profile["api_key"] == "sk-1...abcd"
    assert profile["api_key"] != "sk-1234567890abcd"

    status = _status()
    assert status["source"] == "database"
    assert status["active_profile_id"] == profile["id"]
    assert status["base_url"] == "https://api.deepseek.com"
    assert status["api_key"] == "sk-1...abcd"
    assert status["available"] is True

    # The full key is stored locally (the instance must be able to call out),
    # but it never appears in an API response.
    with SessionLocal() as db:
        stored = llm_repository.get(db, profile["id"])
        assert stored is not None
        assert stored.api_key == "sk-1234567890abcd"


def test_only_one_profile_is_active_and_switching_works():
    first = _create(name="配置一", model="qwen2.5:7b")
    second = _create(
        name="配置二", base_url="http://127.0.0.1:8000/v1", activate=False
    )
    assert second["is_active"] is False

    activated = client.post(f"/api/v1/llm/profiles/{second['id']}/activate")
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True

    profiles = client.get("/api/v1/llm/profiles").json()
    assert {item["id"]: item["is_active"] for item in profiles} == {
        first["id"]: False,
        second["id"]: True,
    }
    assert _status()["active_profile_id"] == second["id"]


def test_deleting_the_active_profile_returns_to_env():
    profile = _create(name="临时配置")
    response = client.delete(f"/api/v1/llm/profiles/{profile['id']}")
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/llm/profiles").json() == []
    assert _status()["source"] == "env"


def test_duplicate_name_is_rejected():
    _create(name="重名配置")
    response = client.post("/api/v1/llm/profiles", json={**LOCAL_PROFILE, "name": "重名配置"})
    assert response.status_code == 409, response.text
    assert "已存在" in response.json()["detail"]


def test_cloud_profile_without_key_is_rejected():
    response = client.post(
        "/api/v1/llm/profiles",
        json={
            "name": "缺 Key",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
            "model": "deepseek-flash",
        },
    )
    assert response.status_code == 422, response.text
    assert "API Key" in response.json()["detail"]


def test_update_keeps_the_stored_key_unless_cleared():
    profile = _create(api_key="sk-abcdefghijklmnop")
    response = client.patch(
        f"/api/v1/llm/profiles/{profile['id']}", json={"name": "改名后"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "改名后"
    assert response.json()["api_key"] == "sk-a...mnop"

    with SessionLocal() as db:
        stored = llm_repository.get(db, profile["id"])
        assert stored is not None
        assert stored.api_key == "sk-abcdefghijklmnop"

    cleared = client.patch(
        f"/api/v1/llm/profiles/{profile['id']}", json={"api_key": ""}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["api_key"] == "(none)"


def test_unknown_profile_is_404():
    assert client.patch("/api/v1/llm/profiles/999", json={}).status_code == 404
    assert client.post("/api/v1/llm/profiles/999/activate").status_code == 404
    assert client.delete("/api/v1/llm/profiles/999").status_code == 404
    assert client.post("/api/v1/llm/test", json={"profile_id": 999}).status_code == 404


def test_test_connection_uses_stored_key_and_reports_status(monkeypatch):
    profile = _create(
        name="可测试",
        base_url="https://api.example.com/v1",
        api_key="sk-testkey1234567890",
        model="demo-model",
    )
    seen: dict[str, Any] = {}

    def fake_probe(config: LLMConfig) -> LLMProbeResult:
        seen["config"] = config
        return LLMProbeResult("ok", "连接成功，模型「demo-model」可用", ("demo-model",))

    monkeypatch.setattr("backend.api.routes.llm.probe_llm_connection", fake_probe)

    response = client.post(
        "/api/v1/llm/test", json={"profile_id": profile["id"], "timeout": 5}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ok",
        "detail": "连接成功，模型「demo-model」可用",
        "models": ["demo-model"],
    }
    assert seen["config"].api_key == "sk-testkey1234567890"


def test_probe_classifies_results():
    config = LLMConfig(
        base_url="https://api.example.com/v1", api_key="sk-x", model="demo"
    )

    ok = probe_llm_connection(
        config, transport=lambda _u, _h, _t: (200, {"data": [{"id": "demo"}]})
    )
    assert ok.status == "ok"

    missing = probe_llm_connection(
        config, transport=lambda _u, _h, _t: (200, {"data": [{"id": "other"}]})
    )
    assert missing.status == "model_missing"

    unauthorized = probe_llm_connection(
        config, transport=lambda _u, _h, _t: (401, {"error": "bad key"})
    )
    assert unauthorized.status == "unauthorized"

    unsupported = probe_llm_connection(
        config, transport=lambda _u, _h, _t: (404, None)
    )
    assert unsupported.status == "unsupported"

    failed = probe_llm_connection(
        config, transport=lambda _u, _h, _t: (500, None)
    )
    assert failed.status == "error"

    def unreachable(_url: str, _headers: dict[str, str], _timeout: float):
        raise LLMCallError("无法连接：拒绝连接")

    down = probe_llm_connection(config, transport=unreachable)
    assert down.status == "unreachable"


def test_probe_requires_a_key_for_cloud_endpoints():
    result = probe_llm_connection(
        LLMConfig(base_url="https://api.example.com/v1", api_key="", model="demo")
    )
    assert result.status == "unauthorized"


@pytest.mark.parametrize(
    "base_url",
    ["api.deepseek.com", "ftp://example.com", "://missing"],
)
def test_invalid_base_url_is_rejected(base_url: str):
    response = client.post(
        "/api/v1/llm/profiles",
        json={**LOCAL_PROFILE, "name": f"坏地址-{base_url}", "base_url": base_url},
    )
    assert response.status_code == 422, response.text
