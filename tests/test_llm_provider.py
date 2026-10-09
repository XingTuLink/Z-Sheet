"""Tests for the OpenAI-compatible LLM client (Day 20).

No real network: a fake transport captures the request and returns canned
response bodies.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.ai.provider import (
    ChatClient,
    LLMCallError,
    LLMConfig,
    LLMConfigError,
    extract_json,
    mask_key,
)

CLOUD_CONFIG = LLMConfig(
    base_url="https://api.deepseek.com",
    api_key="sk-test-1234567890",
    model="deepseek-flash",
    timeout=5.0,
)
LOCAL_CONFIG = LLMConfig(
    base_url="http://localhost:11434/v1",
    api_key="",
    model="qwen2.5:7b",
    timeout=5.0,
)


class FakeTransport:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response: dict[str, Any] = (
            response
            if response is not None
            else {"choices": [{"message": {"content": "你好"}}]}
        )
        self.calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = []

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        self.calls.append((url, headers, payload, timeout))
        return self.response


def test_cloud_requires_key() -> None:
    config = LLMConfig(
        base_url="https://api.deepseek.com", api_key="", model="deepseek-flash"
    )
    assert config.available is False
    transport = FakeTransport()
    with pytest.raises(LLMConfigError):
        ChatClient(config, transport=transport).complete(
            [{"role": "user", "content": "hi"}]
        )
    assert transport.calls == []


def test_local_engine_allowed_keyless() -> None:
    assert LOCAL_CONFIG.is_local is True
    assert LOCAL_CONFIG.available is True
    transport = FakeTransport()
    content = ChatClient(LOCAL_CONFIG, transport=transport).complete(
        [{"role": "user", "content": "hi"}]
    )
    assert content == "你好"


def test_request_shape_cloud() -> None:
    transport = FakeTransport()
    ChatClient(CLOUD_CONFIG, transport=transport).complete(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        temperature=0.2,
    )
    url, headers, payload, timeout = transport.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test-1234567890"
    assert headers["Content-Type"] == "application/json"
    assert payload["model"] == "deepseek-flash"
    assert payload["stream"] is False
    assert payload["temperature"] == 0.2
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert timeout == 5.0


def test_deepseek_thinking_fields_only_for_deepseek_host() -> None:
    transport = FakeTransport()
    ChatClient(CLOUD_CONFIG, transport=transport).complete(
        [{"role": "user", "content": "hi"}]
    )
    payload = transport.calls[0][2]
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"

    local_transport = FakeTransport()
    ChatClient(LOCAL_CONFIG, transport=local_transport).complete(
        [{"role": "user", "content": "hi"}]
    )
    local_payload = local_transport.calls[0][2]
    assert "thinking" not in local_payload
    assert "reasoning_effort" not in local_payload
    assert "Authorization" not in local_transport.calls[0][1]


def test_base_url_trailing_slash_normalized() -> None:
    config = LLMConfig(
        base_url="https://api.deepseek.com/",
        api_key="sk-test-1234567890",
        model="deepseek-flash",
    )
    transport = FakeTransport()
    ChatClient(config, transport=transport).complete(
        [{"role": "user", "content": "hi"}]
    )
    assert transport.calls[0][0] == "https://api.deepseek.com/chat/completions"


def test_missing_model_raises_config_error() -> None:
    config = LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-test-1234567890",
        model="",
    )
    with pytest.raises(LLMConfigError):
        ChatClient(config, transport=FakeTransport()).complete(
            [{"role": "user", "content": "hi"}]
        )


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": 123}}]},
    ],
)
def test_malformed_response_raises_call_error(response: dict[str, Any]) -> None:
    with pytest.raises(LLMCallError):
        ChatClient(CLOUD_CONFIG, transport=FakeTransport(response)).complete(
            [{"role": "user", "content": "hi"}]
        )


def test_transport_exception_becomes_call_error() -> None:
    def raising_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        raise TimeoutError("timed out")

    with pytest.raises(LLMCallError):
        ChatClient(CLOUD_CONFIG, transport=raising_transport).complete(
            [{"role": "user", "content": "hi"}]
        )


def test_complete_json_returns_object() -> None:
    wrapped = '分析如下：\n```json\n{"entities": [], "ok": true}\n```'
    client = ChatClient(
        CLOUD_CONFIG,
        transport=FakeTransport(
            {"choices": [{"message": {"content": wrapped}}]}
        ),
    )
    assert client.complete_json([{"role": "user", "content": "x"}]) == {
        "entities": [],
        "ok": True,
    }


def test_extract_json_plain_and_braces_in_strings() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('结果 {"a": "含有 } 大括号"} 结束') == {
        "a": "含有 } 大括号"
    }
    assert extract_json('```json\n{"x": [1, 2]}\n```') == {"x": [1, 2]}


def test_extract_json_failures() -> None:
    with pytest.raises(LLMCallError):
        extract_json("没有 JSON")
    with pytest.raises(LLMCallError):
        extract_json('{"a": ')


def test_mask_key() -> None:
    assert mask_key("") == "(none)"
    assert mask_key("short") == "***"
    assert mask_key("sk-1234567890abcd") == "sk-1...abcd"
