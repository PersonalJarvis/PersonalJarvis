"""Provider refusals a retry cannot fix reach Hermes / OpenClaw as what they are.

Measured live 2026-10-07: an empty OpenAI balance answers 429, OpenRouter 402,
a stopped local server times out, and Anthropic refuses a Claude subscription
used by Hermes or OpenClaw with a 400 ("Third-party apps now draw from your
extra usage, not your plan limits") or a bare 429 while the plan has room.
All of them used to read as "rate limited, try again in 30 s".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.agent_runtimes import gateway, provider_errors
from jarvis.agent_runtimes.provider_errors import classify, login_refusal


class ProviderStatusError(Exception):
    """The shape the openai / anthropic SDKs raise: a status and a parsed body."""

    def __init__(self, status: int, body: Any = None, message: str = "Error") -> None:
        super().__init__(message)
        self.status_code = status
        self.body = body


class APITimeoutError(Exception):
    """Named like the SDKs' transport error; the name is the signal."""


#: A stand-in Claude login bearer (not a credential).
_BEARER = "login-bearer"

_USAGE_ROOM = {
    "five_hour": {"utilization": 21.0, "resets_at": "2026-10-07T11:09:59+00:00"},
    "seven_day": {"utilization": 54.0, "resets_at": "2026-10-12T12:59:59+00:00"},
}


def test_an_empty_openai_balance_is_billing_not_a_rate_limit() -> None:
    exc = ProviderStatusError(
        429,
        {"error": {"message": "You have no credits remaining.", "code": "insufficient_quota"}},
    )
    refusal = classify("openai", exc)
    assert refusal is not None
    assert (refusal.status, refusal.code) == (402, "billing")
    assert "OpenAI" in refusal.message
    assert "no credits remaining" not in refusal.message  # never the provider's body


def test_payment_required_is_billing() -> None:
    refusal = classify("openrouter", ProviderStatusError(402, {"error": {"code": 402}}))
    assert refusal is not None and refusal.status == 402


def test_a_zero_quota_is_billing() -> None:
    body = {"error": {"message": "Quota exceeded for metric: x, limit: 0, model: m"}}
    refusal = classify("gemini", ProviderStatusError(429, body))
    assert refusal is not None and refusal.code == "billing"


def test_an_ordinary_rate_limit_stays_unclassified() -> None:
    body = {"error": {"type": "rate_limit_error", "message": "Error"}}
    assert classify("claude-api", ProviderStatusError(429, body)) is None
    assert classify("openai", ProviderStatusError(500)) is None


class ConnectTimeout(Exception):
    """httpx's connect timeout: nothing accepted the connection."""


class ReadTimeout(Exception):
    """httpx's read timeout: the server was reached and went quiet."""


def _timeout(cause: Exception) -> APITimeoutError:
    """An SDK timeout wrapping the transport error, as openai/anthropic raise it."""
    try:
        raise APITimeoutError("Request timed out.") from cause
    except APITimeoutError as exc:
        return exc


def test_a_stopped_local_server_is_unreachable() -> None:
    # Windows refuses a closed localhost port only after the connect timeout.
    refusal = classify("local-openai", _timeout(ConnectTimeout("connect timed out")))
    assert refusal is not None
    assert (refusal.status, refusal.code) == (503, "provider_unreachable")


def test_a_wrapped_transport_error_is_found_in_the_chain() -> None:
    try:
        try:
            raise ConnectTimeout("timed out")
        except ConnectTimeout as inner:
            raise RuntimeError("stream failed") from inner
    except RuntimeError as outer:
        refusal = classify("ollama", outer)
    assert refusal is not None and refusal.code == "provider_unreachable"


def test_a_model_that_took_too_long_is_a_timeout_not_unreachable() -> None:
    refusal = classify("openai", _timeout(ReadTimeout("read timed out")), "gpt-5.5")
    assert refusal is not None
    assert (refusal.status, refusal.code) == (504, "timeout")
    assert "took too long" in refusal.message and "start it" not in refusal.message


_OVERFLOWS = [
    # OpenAI / OpenRouter
    ProviderStatusError(
        400,
        {"error": {"code": "context_length_exceeded", "type": "invalid_request_error"}},
        "This model's maximum context length is 400000 tokens.",
    ),
    # Anthropic
    ProviderStatusError(
        400,
        {"type": "error", "error": {"type": "invalid_request_error"}},
        "prompt is too long: 210000 tokens > 200000 maximum",
    ),
    # vLLM / local servers
    ProviderStatusError(400, None, "max_tokens is too large: this exceeds max_model_len"),
    # A stream error event without a status
    RuntimeError("Responses API stream failed: Your input exceeds the context window"),
]


@pytest.mark.parametrize("exc", _OVERFLOWS)
def test_a_context_overflow_tells_the_runtime_to_compress(exc: Exception) -> None:
    refusal = provider_errors.gateway_refusal("openai", exc, "gpt-5.5")
    assert (refusal.status, refusal.code) == (400, "context_length_exceeded")
    # Hermes and OpenClaw both match these words to compress and resend.
    assert "context length exceeded" in refusal.message.lower()
    assert not provider_errors.is_terminal(refusal.code)


def test_a_gemini_overflow_is_found_on_the_genai_error_shape() -> None:
    class ClientError(Exception):  # google-genai: ``code`` int, ``status`` name
        def __init__(self) -> None:
            super().__init__("400 INVALID_ARGUMENT. The input token count (1200000) exceeds "
                             "the maximum number of tokens allowed (1048576).")
            self.code = 400
            self.status = "INVALID_ARGUMENT"
            self.details = {"error": {"code": 400, "status": "INVALID_ARGUMENT"}}

    refusal = provider_errors.gateway_refusal("gemini", ClientError())
    assert refusal.code == "context_length_exceeded"


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (ProviderStatusError(404, {"error": {"code": "model_not_found"}}, "no such model"),
         404, "model_not_found"),
        (ProviderStatusError(400, None, "registry.ollama.ai/library/gemma:2b does not support "
                             "tools"), 400, "tools_unsupported"),
        (ProviderStatusError(400, {"error": {"code": "invalid_function_parameters"}},
                             "Invalid schema for function 'x'"), 400, "invalid_tool_schema"),
        (ProviderStatusError(400, None, "messages.3: unexpected role"), 400, "invalid_request"),
        (ProviderStatusError(529, None, "Overloaded"), 503, "provider_overloaded"),
        (ProviderStatusError(500, None, "boom"), 502, "provider_error"),
        (ProviderStatusError(401, None, "invalid x-api-key"), 401, "provider_auth"),
    ],
)
def test_every_provider_failure_gets_an_actionable_status(exc, status, code) -> None:
    refusal = provider_errors.gateway_refusal("ollama", exc, "gemma:2b")
    assert (refusal.status, refusal.code) == (status, code)
    assert "must never" not in refusal.message


def test_only_unrecoverable_failures_end_the_turn() -> None:
    terminal = {"billing", "extra_usage_off", "provider_auth", "claude_login_expired",
                "model_not_found", "invalid_tool_schema", "tools_unsupported", "rate_limited"}
    recoverable = {"context_length_exceeded", "timeout", "provider_overloaded",
                   "provider_error", "invalid_request", "provider_unreachable"}
    assert all(provider_errors.is_terminal(code) for code in terminal)
    assert not any(provider_errors.is_terminal(code) for code in recoverable)


def test_the_log_line_is_bounded_and_masks_credentials() -> None:
    exc = ProviderStatusError(
        401, {"error": {"code": "invalid_api_key"}},
        "Incorrect API key provided: sk-proj-abcdefghijklmnop. Bearer abcdefghijklmnopqrst "
        + "x" * 900,
    )
    line = provider_errors.describe(exc)
    assert "sk-proj-abcdefghijklmnop" not in line and "abcdefghijklmnopqrst" not in line
    assert "status=401" in line and "invalid_api_key" in line
    assert len(line) < 520


def test_extra_usage_off_explains_a_claude_login_refusal() -> None:
    usage = {**_USAGE_ROOM, "extra_usage": {"is_enabled": False, "user_disabled": True}}
    refusal = login_refusal(usage, "claude-sonnet-5-5")
    assert refusal is not None
    assert (refusal.status, refusal.code) == (402, "extra_usage_off")
    assert "claude-sonnet-5-5" in refusal.message
    assert "Extra Usage" in refusal.message


def test_a_spent_extra_usage_limit_is_named() -> None:
    usage = {**_USAGE_ROOM, "extra_usage": {"is_enabled": True, "spend_limit_reached": True}}
    refusal = login_refusal(usage, "claude-opus-5-5")
    assert refusal is not None and refusal.code == "extra_usage_spent"


def test_anthropics_third_party_wording_is_extra_usage() -> None:
    body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Third-party apps now draw from your extra usage, not your plan "
            "limits. Add more at claude.ai/settings/usage and keep going.",
        },
    }
    refusal = classify("claude-api", ProviderStatusError(400, body))
    assert refusal is not None
    assert (refusal.status, refusal.code) == (402, "extra_usage_off")


def test_a_plan_window_says_nothing_about_third_party_use() -> None:
    usage = {"five_hour": {"utilization": 100.0}, "extra_usage": {"is_enabled": True}}
    assert login_refusal(usage, "claude-haiku-4-5") is None


def test_a_blocked_login_is_known_before_any_model_call(monkeypatch) -> None:
    calls: list[str] = []

    def usage(token: str) -> dict[str, Any]:
        calls.append(token)
        return {**_USAGE_ROOM, "extra_usage": {"is_enabled": False}}

    monkeypatch.setattr(provider_errors, "_claude_usage", usage)
    monkeypatch.setattr(provider_errors, "_REPORTS", {})
    first = provider_errors.login_blocked(_BEARER, "claude-haiku-4-5")
    second = provider_errors.login_blocked(_BEARER, "claude-haiku-4-5")
    assert first is not None and first.code == "extra_usage_off"
    assert second == first
    assert calls == [_BEARER]  # cached: routing asks once, not every turn


def test_a_report_without_an_explanation_changes_nothing() -> None:
    usage = {**_USAGE_ROOM, "extra_usage": {"is_enabled": True, "spend_limit_reached": False}}
    assert login_refusal(usage, "claude-sonnet-5-5") is None
    assert login_refusal(None, "claude-sonnet-5-5") is None


def test_the_gateway_reports_the_refusal_not_a_rate_limit() -> None:
    failure = gateway._failure(
        "openai", ProviderStatusError(429, {"error": {"code": "insufficient_quota"}})
    )
    assert (failure.status, failure.code) == (402, "billing")


async def test_a_claude_login_429_is_explained_from_the_usage_report(monkeypatch) -> None:
    """The gateway's own plugin path: login answers, Anthropic says 429."""
    import jarvis.agent_runtimes.model_map as model_map
    import jarvis.core.config as config
    import jarvis.plugins.brain.claude_api as claude_api
    from jarvis.core.protocols import BrainMessage, BrainRequest

    class RefusingBrain:
        def __init__(self, model: str | None = None, *, auth_token: str | None = None) -> None:
            assert auth_token == _BEARER

        async def complete(self, request: Any) -> AsyncIterator[Any]:
            raise ProviderStatusError(429, {"error": {"type": "rate_limit_error"}})
            yield  # pragma: no cover — makes this an async generator

    seen: list[str] = []

    def usage(token: str) -> dict[str, Any]:
        seen.append(token)
        return {**_USAGE_ROOM, "extra_usage": {"is_enabled": False}}

    monkeypatch.setattr(model_map, "login_route", lambda provider, account: (True, _BEARER))
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    monkeypatch.setattr(claude_api, "ClaudeAPIBrain", RefusingBrain)
    monkeypatch.setattr(provider_errors, "_claude_usage", usage)
    grant = gateway.Grant("agent-1", "claude-api", "subscription")
    request = BrainRequest(messages=(BrainMessage("user", "Hi"),), max_tokens=16)

    with pytest.raises(provider_errors.ProviderRefusal) as caught:
        async for _ in gateway._deltas(grant, "claude-sonnet-5-5", request):
            pass
    assert seen == [_BEARER]
    failure = gateway._failure("claude-api", caught.value)
    assert (failure.status, failure.code) == (402, "extra_usage_off")


async def test_a_non_streaming_subscription_answer_keeps_its_output(monkeypatch) -> None:
    """ChatGPT's backend streams the items and completes with an empty output."""
    item = {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}

    class Subscription:
        async def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "response.output_item.done", "item": item}
            yield {"type": "response.completed", "response": {"id": "r1", "output": []}}

    gateway.reset()
    monkeypatch.setattr(gateway, "_client", lambda account_id: Subscription())
    try:
        answer = await gateway.complete_response(
            gateway.Grant("agent-1", "openai-codex"),
            {"model": "gpt-5.6-sol", "input": [], "instructions": "", "tools": []},
        )
    finally:
        gateway.reset()
    assert answer["output"] == [item]


def test_a_route_refuses_a_blocked_claude_login_before_the_runtime_starts(monkeypatch) -> None:
    import jarvis.agent_runtimes.model_map as model_map

    monkeypatch.setattr(model_map, "login_token_for", lambda provider, account: _BEARER)
    blocked = provider_errors._extra_usage_off
    monkeypatch.setattr(provider_errors, "login_blocked", lambda token, model: blocked(model))
    with pytest.raises(model_map.RouteUnavailable, match="Extra Usage"):
        model_map._check_login_billing("claude-api", "claude-haiku-4-5", "")


def test_an_api_key_route_never_reads_the_login(monkeypatch) -> None:
    import jarvis.agent_runtimes.model_map as model_map

    monkeypatch.setattr(model_map, "login_token_for", lambda provider, account: None)
    model_map._check_login_billing("claude-api", "claude-haiku-4-5", "api-key")


def test_a_system_message_moves_into_the_instructions() -> None:
    """ChatGPT's backend refuses system input items (OpenClaw sends one)."""
    args = gateway.request_args(
        {
            "model": "gpt-6.1-sol",
            "instructions": "",
            "input": [
                {"type": "message", "role": "system", "content": "You are Lumen."},
                {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "Hi"},
                ]},
            ],
        }
    )
    assert args["instructions"] == "You are Lumen."
    assert [item["role"] for item in args["input"]] == ["user"]
