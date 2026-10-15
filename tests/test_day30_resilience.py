"""Day 30: AI 故障韧性与 Patch 回滚验收。

覆盖外部依赖失败时的四条承诺：

1. 失败可分类 —— 超时(LLMTimeoutError)/HTTP 状态(LLMHTTPError)/网络/坏输出；
2. 失败可恢复 —— 瞬时故障（超时、5xx/429、坏 JSON）在小预算内自动重试；
3. 失败可降级 —— 理解层重试耗尽后自动退回规则引擎，SSE 仍正常产出结果，
   无规则兜底的 NL Patch 返回明确的 503/502，而不是裸 500；
4. 修改可回滚 —— 高风险自然语言删除经确认后，可撤销/回滚到任意历史版本，
   且版本链始终 append-only。

全部离线：传输层用脚本化 fake，重试睡眠注入空操作。
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import openpyxl
import pytest
from fastapi.testclient import TestClient

from backend.ai import provider
from backend.ai.provider import (
    ChatClient,
    LLMCallError,
    LLMConfig,
    LLMHTTPError,
    LLMTimeoutError,
)
from backend.api.main import app, create_app
from backend.api.routes.patch import get_chat_client
from backend.ingestion.parser import parse_workbook
from backend.understanding.profiling import add_profiles

# A distinctive fake secret: long enough for mask_key's first4...last4 form.
SECRET_KEY = "sk-SECRET1234WXYZ"
MASKED_KEY = "sk-S...WXYZ"

client = TestClient(app)


def _config(*, max_retries: int = 1) -> LLMConfig:
    return LLMConfig(
        base_url="https://api.deepseek.com",
        api_key=SECRET_KEY,
        model="deepseek-flash",
        timeout=1.0,
        max_retries=max_retries,
    )


def _completion(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


class ScriptedTransport:
    """Transport returning/raising from a scripted per-call outcome list."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.sleeps: list[float] = []

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        index = self.calls
        self.calls += 1
        outcome = self.outcomes[min(index, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[no-any-return]

    @property
    def sleeper(self) -> Callable[[float], None]:
        def _sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        return _sleep


def _client(transport: Any, *, max_retries: int = 1) -> ChatClient:
    return ChatClient(
        _config(max_retries=max_retries),
        transport=transport,
        sleeper=getattr(transport, "sleeper", lambda _: None),
    )


# --- 1. 失败分类 -----------------------------------------------------------------


def test_urllib_classifies_wrapped_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.example.com/v1/chat/completions"

    def _raise(request: Any, timeout: float = 0, **_: Any) -> Any:
        raise urllib.error.URLError(TimeoutError("read timed out"))

    monkeypatch.setattr(provider.urllib.request, "urlopen", _raise)
    with pytest.raises(LLMTimeoutError, match="超时"):
        provider._urllib_transport(url, {}, {}, 1.0)


def test_urllib_classifies_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.example.com/v1/chat/completions"

    def _raise_500(request: Any, timeout: float = 0, **_: Any) -> Any:
        raise urllib.error.HTTPError(
            url,
            500,
            "Server Error",
            email.message.EmailMessage(),
            io.BytesIO(b'{"error":"busy"}'),
        )

    monkeypatch.setattr(provider.urllib.request, "urlopen", _raise_500)
    with pytest.raises(LLMHTTPError) as exc_info:
        provider._urllib_transport(url, {}, {}, 1.0)
    assert exc_info.value.status_code == 500
    assert exc_info.value.retriable is True

    # 401 is a definitive rejection: never worth a retry.
    auth_error = LLMHTTPError("HTTP 401", status_code=401)
    assert auth_error.retriable is False


# --- 2. 有限重试 -----------------------------------------------------------------


def test_retries_once_after_500_then_succeeds() -> None:
    transport = ScriptedTransport(
        [LLMHTTPError("HTTP 500", status_code=500), _completion("ok")]
    )
    text = _client(transport, max_retries=1).complete([])
    assert text == "ok"
    assert transport.calls == 2
    assert transport.sleeps == [0.5]


def test_timeout_retried_until_budget_exhausted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = ScriptedTransport([LLMTimeoutError("超时"), LLMTimeoutError("超时")])
    with pytest.raises(LLMTimeoutError):
        _client(transport, max_retries=1).complete([])
    assert transport.calls == 2
    assert transport.sleeps == [0.5]
    assert any("重试" in record.getMessage() for record in caplog.records)


def test_401_fails_fast_without_retry() -> None:
    transport = ScriptedTransport([LLMHTTPError("HTTP 401", status_code=401)])
    with pytest.raises(LLMHTTPError) as exc_info:
        _client(transport, max_retries=3).complete([])
    assert exc_info.value.status_code == 401
    assert transport.calls == 1
    assert transport.sleeps == []


def test_garbage_model_output_is_retried_then_parsed() -> None:
    proposal = {"entities": [], "links": []}
    transport = ScriptedTransport(
        [
            _completion("抱歉，我无法理解这个请求。"),  # no JSON at all
            _completion(json.dumps(proposal)),
        ]
    )
    data = _client(transport, max_retries=1).complete_json([])
    assert data == proposal
    assert transport.calls == 2


def test_truncated_json_eventually_fails_after_retries() -> None:
    broken = '{"entities": [{"source_sheet": "客户"'  # truncated mid-object
    transport = ScriptedTransport([_completion(broken), _completion(broken)])
    with pytest.raises(LLMCallError):
        _client(transport, max_retries=1).complete_json([])
    assert transport.calls == 2


def test_missing_choices_is_retried() -> None:
    transport = ScriptedTransport([{"unexpected": True}, {"unexpected": True}])
    with pytest.raises(LLMCallError, match="choices"):
        _client(transport, max_retries=1).complete([])
    assert transport.calls == 2


def test_max_retries_zero_means_single_attempt() -> None:
    transport = ScriptedTransport([LLMTimeoutError("超时")])
    with pytest.raises(LLMTimeoutError):
        _client(transport, max_retries=0).complete([])
    assert transport.calls == 1
    assert transport.sleeps == []


# --- 3. 理解层自动降级 -------------------------------------------------------------


def _two_sheet_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.worksheets[0]
    ws.title = "客户"
    ws.append(["客户名称", "区域", "联系电话"])
    ws.append(["杭州云栖", "华东", "13800000001"])
    ws2 = wb.create_sheet("订单")
    ws2.append(["订单编号", "客户名称", "金额", "下单日期"])
    ws2.append(["SO-1", "杭州云栖", 100, "2026-01-01"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_understanding_falls_back_to_rules_on_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    workbook = parse_workbook("台账.xlsx", _two_sheet_xlsx())
    timeout_error = LLMTimeoutError("LLM 请求超时（1s）：read timed out")
    failing = ChatClient(
        _config(max_retries=0),
        transport=ScriptedTransport([timeout_error]),
    )

    result = add_profiles(workbook, llm_client=failing)

    assert result.understanding_engine == "rules"
    assert any(note.startswith("LLM 理解失败") for note in result.understanding_notes)
    assert any("超时" in note for note in result.understanding_notes)
    # The deterministic model is fully intact despite the dead LLM.
    assert result.business_model is not None
    model = cast(dict[str, Any], result.business_model)
    assert {e["key"] for e in model["entities"]} == {
        "customer",
        "order",
    }
    # The degradation must be observable in the logs.
    assert any("降级" in record.getMessage() for record in caplog.records)
    # Logs must never carry the API key, even in a failure path.
    assert SECRET_KEY not in caplog.text


def test_sse_parse_survives_llm_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead cloud LLM must still end the SSE stream in a rules result."""
    monkeypatch.setenv("ZSHEET_LLM_API_KEY", SECRET_KEY)
    monkeypatch.setenv("ZSHEET_LLM_MAX_RETRIES", "0")

    def _timeout(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise LLMTimeoutError("read timed out")

    monkeypatch.setattr(provider, "_urllib_transport", _timeout)

    response = client.post(
        "/api/v1/ingestion/parse/stream",
        files={
            "file": (
                "台账.xlsx",
                _two_sheet_xlsx(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200

    frames: list[tuple[str, str]] = []
    for block in response.text.strip().split("\n\n"):
        event = data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        frames.append((event, data))

    kinds = [event for event, _ in frames]
    assert "error" not in kinds
    assert kinds[-1] == "result"

    payload = json.loads(frames[-1][1])
    assert payload["understanding_engine"] == "rules"
    assert any("LLM 理解失败" in note for note in payload["understanding_notes"])
    assert payload["business_model"] is not None


# --- 4. NL Patch 的明确失败态 ------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _import_app(app_key: str = "day30") -> None:
    sales_yaml = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "sales_management.yaml"
    ).read_text(encoding="utf-8")
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": sales_yaml},
    )
    assert response.status_code == 201, response.text


def _preview(app_key: str = "day30", *, text: str = "随便改点什么") -> Any:
    return client.post(
        f"/api/v1/models/{app_key}/patch-sessions",
        json={"text": text},
    )


def test_patch_preview_timeout_is_503_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _import_app()
    failing = ChatClient(
        _config(max_retries=0),
        transport=ScriptedTransport([LLMTimeoutError("read timed out")]),
    )
    app.dependency_overrides[get_chat_client] = lambda: failing

    response = _preview()
    assert response.status_code == 503
    assert "暂时不可用" in response.json()["detail"]
    assert any("LLM 调用失败" in record.getMessage() for record in caplog.records)


def test_patch_preview_recovers_after_transient_timeout() -> None:
    _import_app()
    intent = {
        "intents": [
            {
                "kind": "field_add",
                "entity": "客户",
                "params": {"name": "微信号", "type": "string"},
                "confidence": 0.95,
            }
        ]
    }
    transport = ScriptedTransport(
        [LLMTimeoutError("read timed out"), _completion(json.dumps(intent))]
    )
    app.dependency_overrides[get_chat_client] = lambda: _client(
        transport, max_retries=1
    )

    response = _preview(text="客户里加一个微信号字段")
    assert response.status_code == 201, response.text
    assert transport.calls == 2
    assert response.json()["actionable"] is True


def test_patch_preview_structural_garbage_is_502() -> None:
    _import_app()
    # The wire schema is deliberately lenient (grounding misses become
    # notes), so only a real shape violation reaches the ValueError->502
    # path: intents must be a list, not a bare string.
    broken = ChatClient(
        _config(max_retries=0),
        transport=ScriptedTransport(
            [_completion(json.dumps({"intents": "field_add"}))]
        ),
    )
    app.dependency_overrides[get_chat_client] = lambda: broken

    response = _preview()
    assert response.status_code == 502
    assert "意图解析失败" in response.json()["detail"]


# --- 5. 破坏性自然语言修改的回滚验收 -------------------------------------------------


def _nl_patch(intent: dict[str, Any], text: str, app_key: str = "day30") -> str:
    transport = ScriptedTransport([_completion(json.dumps(intent))])
    app.dependency_overrides[get_chat_client] = lambda: ChatClient(
        _config(max_retries=0), transport=transport
    )
    response = _preview(app_key, text=text)
    assert response.status_code == 201, response.text
    return str(response.json()["session_id"])


def _confirm(session_id: str, app_key: str = "day30") -> dict[str, Any]:
    response = client.post(
        f"/api/v1/models/{app_key}/patch-sessions/{session_id}/confirm"
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _customer_field_keys(app_key: str = "day30") -> set[str]:
    snapshot = client.get(f"/api/v1/models/{app_key}").json()["snapshot"]
    customer = next(e for e in snapshot["entities"] if e["key"] == "customer")
    return {f["key"] for f in customer["fields"]}


def _customer_field_names(app_key: str = "day30") -> set[str]:
    snapshot = client.get(f"/api/v1/models/{app_key}").json()["snapshot"]
    customer = next(e for e in snapshot["entities"] if e["key"] == "customer")
    return {f["name"] for f in customer["fields"]}


def test_destructive_nl_patch_is_undoable_and_rollbackable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="zsheet")
    _import_app()

    # v2: a high-risk NL deletion (confirmed) removes the phone field.
    remove_intent = {
        "intents": [
            {
                "kind": "field_remove",
                "entity": "客户",
                "field": "联系电话",
                "confidence": 0.9,
            }
        ]
    }
    session_id = _nl_patch(remove_intent, "把客户的联系电话删掉")
    removed = _confirm(session_id)
    assert removed["version"] == 2
    assert "phone" not in _customer_field_keys()

    # v3: undo restores the previous snapshot (phone is back).
    undone = client.post("/api/v1/models/day30/undo", json={})
    assert undone.status_code == 201, undone.text
    assert undone.json()["version"] == 3
    assert "phone" in _customer_field_keys()

    # v4: work continues with another NL change on top of the restored model.
    add_intent = {
        "intents": [
            {
                "kind": "field_add",
                "entity": "客户",
                "params": {"name": "微信号", "type": "string"},
                "confidence": 0.95,
            }
        ]
    }
    added = _confirm(_nl_patch(add_intent, "客户里加一个微信号字段"))
    assert added["version"] == 4
    names = _customer_field_names()
    assert "联系电话" in names
    assert "微信号" in names

    # v5: rollback to v1 restores the exact original snapshot; the wechat
    # field is gone and history stays append-only.
    v1 = client.get("/api/v1/models/day30/versions/1").json()
    rolled = client.post("/api/v1/models/day30/versions/1/rollback", json={})
    assert rolled.status_code == 201, rolled.text
    assert rolled.json()["version"] == 5
    assert rolled.json()["snapshot"] == v1["snapshot"]
    restored_names = _customer_field_names()
    assert "联系电话" in restored_names
    assert "微信号" not in restored_names

    versions = client.get("/api/v1/models/day30/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3, 4, 5]
    assert any("回滚" in record.getMessage() for record in caplog.records)


# --- 6. 启动日志与密钥安全 ----------------------------------------------------------


def test_startup_log_masks_api_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ZSHEET_LLM_API_KEY", SECRET_KEY)
    caplog.set_level("INFO", logger="zsheet")
    create_app()

    assert SECRET_KEY not in caplog.text
    assert MASKED_KEY in caplog.text
