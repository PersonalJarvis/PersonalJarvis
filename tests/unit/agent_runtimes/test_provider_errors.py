"""Provider refusals a retry cannot fix reach Hermes / OpenClaw as what they are.

Measured live 2026-10-07: an empty OpenAI balance answers 429, OpenRouter 402,
a stopped local server times out, and Anthropic refuses a Claude subscription
outside Claude Code with a bare 429 while the plan has room (Extra Usage off).
All of them used to read as "rate limited, try again in 30 s".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
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


def test_a_stopped_local_server_is_unreachable() -> None:
    refusal = classify("local-openai", APITimeoutError("Request timed out."))
    assert refusal is not None
    assert (refusal.status, refusal.code) == (503, "provider_unreachable")


def test_a_wrapped_transport_error_is_found_in_the_chain() -> None:
    try:
        try:
            raise APITimeoutError("timed out")
        except APITimeoutError as inner:
            raise RuntimeError("stream failed") from inner
    except RuntimeError as outer:
        refusal = classify("ollama", outer)
    assert refusal is not None and refusal.code == "provider_unreachable"


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


def test_a_used_up_plan_window_waits_until_its_reset() -> None:
    usage = {
        "five_hour": {"utilization": 100.0, "resets_at": "2026-10-07T12:00:00+00:00"},
        "extra_usage": {"is_enabled": False},
    }
    now = datetime(2026, 10, 7, 11, 0, tzinfo=UTC)
    refusal = login_refusal(usage, "claude-sonnet-5-5", now=now)
    assert refusal is not None
    assert refusal.status == 429
    assert refusal.retry_after == pytest.approx(3600)
    assert "5-hour" in refusal.message


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

    monkeypatch.setattr(model_map, "login_token_for", lambda provider, account: _BEARER)
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
