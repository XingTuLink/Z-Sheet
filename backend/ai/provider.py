"""OpenAI-compatible chat completion client (Day 20).

One client for every backend: public clouds (DeepSeek, Qwen, OpenAI-compatible
gateways) and local engines (Ollama, vLLM). All of them speak the same
``POST {base_url}/chat/completions`` contract; differences are limited to:

- cloud endpoints require ``Authorization: Bearer <key>``; local engines
  (localhost / 127.0.0.1) usually accept any or no key;
- DeepSeek's ``deepseek-flash`` accepts ``thinking`` / ``reasoning_effort``;
  those fields are only sent to api.deepseek.com so strict local engines do
  not reject the request.

The HTTP layer is the standard library (urllib) on purpose: Z-Sheet must stay
easy to self-deploy and this is a single non-streaming JSON POST. A transport
callable can be injected for tests.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# A transport performs one POST and returns the decoded response body.
Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]
# Injectable sleep so retry-backoff tests stay instant and deterministic.
Sleeper = Callable[[float], None]

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

logger = logging.getLogger("zsheet.llm")

# HTTP statuses worth one more attempt: rate limiting, timeouts, gateway blips.
_RETRIABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Base class for LLM problems; callers may treat all of them as fallback."""


class LLMConfigError(LLMError):
    """The LLM is not usable with the current configuration (e.g. no API key)."""


class LLMCallError(LLMError):
    """The request failed on the network or the response was unusable."""


class LLMTimeoutError(LLMCallError):
    """The request exceeded the configured timeout (retry-worthy)."""


class LLMHTTPError(LLMCallError):
    """The API answered with a non-2xx HTTP status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def retriable(self) -> bool:
        return self.status_code in _RETRIABLE_STATUSES


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0
    enable_thinking: bool = True
    # Retries *in addition to* the first attempt (so 1 = up to 2 calls).
    max_retries: int = 1

    @property
    def host(self) -> str:
        return (urlparse(self.base_url).hostname or "").lower()

    @property
    def is_local(self) -> bool:
        return self.host in _LOCAL_HOSTS

    @property
    def available(self) -> bool:
        """Cloud services need a key; local engines are allowed keyless."""
        return self.is_local or bool(self.api_key)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def mask_key(api_key: str) -> str:
    """Return a log-safe representation of an API key (first4...last4)."""
    if not api_key:
        return "(none)"
    if len(api_key) <= 10:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def _urllib_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LLMHTTPError(
            f"LLM 请求失败：HTTP {exc.code} {detail}", status_code=exc.code
        ) from exc
    except urllib.error.URLError as exc:
        # A read/connect timeout arrives wrapped: URLError(reason=timeout()).
        if isinstance(exc.reason, TimeoutError):
            raise LLMTimeoutError(
                f"LLM 请求超时（{timeout:.0f}s）：{exc.reason}"
            ) from exc
        raise LLMCallError(f"LLM 网络错误：{exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMTimeoutError(f"LLM 请求超时（{timeout:.0f}s）：{exc}") from exc
    except OSError as exc:
        raise LLMCallError(f"LLM 网络错误：{exc}") from exc
    try:
        return json.loads(body)  # type: ignore[no-any-return]
    except json.JSONDecodeError as exc:
        raise LLMCallError("LLM 返回了非 JSON 响应") from exc


def extract_json(content: str) -> dict[str, Any]:
    """Pull a JSON object out of model text (handles fences and prose).

    Models often wrap output in ```json blocks or add explanation around it.
    Strategy: strip code fences, then take the first ``{`` … last ``}`` span
    with brace matching that honors quoted strings. Raises LLMCallError when
    no complete object can be parsed.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    if start == -1:
        raise LLMCallError("LLM 输出中没有 JSON 对象")

    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : pos + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    break
                if isinstance(parsed, dict):
                    return parsed
    raise LLMCallError("LLM 输出中的 JSON 对象不完整或无法解析")


class ChatClient:
    """Minimal OpenAI-compatible chat client with bounded retries.

    Transient failures (timeouts, connection errors, 408/429/5xx, unusable
    model output) are retried up to ``config.max_retries`` times with a short
    linear backoff. Configuration errors and definitive HTTP rejections
    (400/401/403/404…) fail immediately. Retries must stay small: the
    understanding pipeline treats a failed call as "use the rules engine",
    so waiting long would only delay a perfectly good fallback.
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: Transport | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or _urllib_transport
        self._sleeper = sleeper or time.sleep

    @property
    def config(self) -> LLMConfig:
        return self._config

    def _with_retry(self, attempt: Callable[[], Any]) -> Any:
        attempts = max(1, self._config.max_retries + 1)
        error: LLMCallError | None = None
        retriable = False
        for index in range(attempts):
            try:
                return attempt()
            except LLMConfigError:
                raise  # nothing transient about missing model/key
            except LLMHTTPError as exc:
                error, retriable = exc, exc.retriable
            except LLMCallError as exc:
                error, retriable = exc, True
            if index == attempts - 1 or not retriable:
                assert error is not None
                raise error
            backoff = 0.5 * (index + 1)
            assert error is not None
            logger.warning(
                "LLM 调用第 %d/%d 次失败（%s），%.1f 秒后重试",
                index + 1,
                attempts,
                error,
                backoff,
            )
            self._sleeper(backoff)
        # Unreachable: the last failed attempt raises above.
        raise LLMCallError("LLM 调用失败")  # pragma: no cover

    def _complete_once(
        self,
        messages: Sequence[dict[str, str]],
        temperature: float,
    ) -> str:
        config = self._config
        if not config.model:
            raise LLMConfigError("未配置 LLM 模型（ZSHEET_LLM_MODEL）")
        if not config.available:
            raise LLMConfigError("未配置 LLM API Key（ZSHEET_LLM_API_KEY）")

        payload: dict[str, Any] = {
            "model": config.model,
            "messages": list(messages),
            "temperature": temperature,
            "stream": False,
        }
        if config.enable_thinking and config.host.endswith("deepseek.com"):
            # deepseek-flash reasoning controls; intentionally scoped to the
            # official endpoint — other OpenAI-compatible engines reject these.
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "high"

        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        try:
            data = self._transport(
                config.endpoint, headers, payload, config.timeout
            )
        except LLMError:
            raise
        except Exception as exc:  # transport failures (timeout, connection…)
            raise LLMCallError(f"LLM 网络错误：{exc}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMCallError("LLM 响应缺少 choices[0].message.content") from exc
        if not isinstance(content, str):
            raise LLMCallError("LLM 响应 content 不是字符串")
        return content

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.0,
    ) -> str:
        """Call chat/completions and return the assistant message content.

        Raises LLMConfigError when the backend is not usable (caller should
        fall back to the deterministic engine) and LLMCallError on network or
        protocol failures. Transient failures are retried per config.
        """
        return self._with_retry(lambda: self._complete_once(messages, temperature))

    def complete_json(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """complete() + JSON extraction, inside the same retry budget.

        Garbage model text (no JSON / truncated object) counts as transient:
        a second completion frequently succeeds.
        """

        def _attempt() -> dict[str, Any]:
            return extract_json(
                self._complete_once(messages, temperature)
            )

        return self._with_retry(_attempt)
