"""Direct-provider handling of the ``reasoning_effort`` request hint.

CU steps and delegated voice turns send ``reasoning_effort="none"`` with a
small ``max_tokens`` budget (256): a reasoning-by-default GPT-5.x model that
never receives the hint burns the whole budget on hidden thought and streams
back empty/truncated JSON — the field report was "OpenAI does not work at
all as the Tool Model". The shared OpenAI-compatible base must:

  1. forward ``reasoning_effort`` on the wire call,
  2. degrade gradually on explicit rejection ("none" → "minimal" → omitted),
  3. strip the kwarg when an old SDK raises ``TypeError`` for it,
  4. pass gateway ``extra_body`` AS ``extra_body`` — a top-level
     ``kwargs.update(extra_body)`` raises ``TypeError`` on every modern SDK
     (``create()`` accepts no ``**kwargs``), which killed every OpenRouter
     call that carried the reasoning opt-out — and never double-send the
     native param next to a gateway reasoning directive.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain import _openai_base
from jarvis.plugins.brain._openai_base import (
    _create_with_token_param_retry,
    stream_complete,
)


@pytest.fixture(autouse=True)
def _fresh_adaptation_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rejected-param memory is process-global; isolate it per test."""
    monkeypatch.setattr(_openai_base, "_PARAM_ADAPTATION_CACHE", {})

_REJECT_VALUE_400 = (
    "Error code: 400 - {'error': {'message': \"Unsupported value: "
    "'reasoning_effort' does not support 'none' with this model.\", "
    "'type': 'invalid_request_error', 'param': 'reasoning_effort', "
    "'code': 'unsupported_value'}}"
)

_REJECT_PARAM_400 = (
    "Error code: 400 - {'error': {'message': \"Unsupported parameter: "
    "'reasoning_effort'.\", 'type': 'invalid_request_error', "
    "'param': 'reasoning_effort', 'code': 'unsupported_parameter'}}"
)


class _EmptyStream:
    def __aiter__(self) -> _EmptyStream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _SequenceClient:
    """create() raises the queued errors first, then returns an empty stream."""

    def __init__(self, errors: list[Exception] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._errors = list(errors or [])
        chat = type("Chat", (), {})()
        chat.completions = type("Completions", (), {})()
        chat.completions.create = self._create
        self.chat = chat

    async def _create(self, **kwargs: Any):
        self.calls.append(kwargs)
        if self._errors:
            raise self._errors.pop(0)
        return _EmptyStream()


def _req(**kw: Any) -> BrainRequest:
    return BrainRequest(messages=(BrainMessage(role="user", content="hi"),), **kw)


async def _drain(client: _SequenceClient, req: BrainRequest, **kw: Any) -> None:
    async for _ in stream_complete(client, "some-model", req, **kw):
        pass


# ---------------------------------------------------------------------------
# 1. The hint reaches the wire call.
# ---------------------------------------------------------------------------


async def test_reasoning_effort_none_is_forwarded() -> None:
    client = _SequenceClient()
    await _drain(client, _req(reasoning_effort="none"))
    assert client.calls[0]["reasoning_effort"] == "none"


async def test_no_hint_sends_no_reasoning_parameter() -> None:
    client = _SequenceClient()
    await _drain(client, _req())
    assert "reasoning_effort" not in client.calls[0]


# ---------------------------------------------------------------------------
# 2. Graded server-side degradation.
# ---------------------------------------------------------------------------


async def test_value_rejection_downgrades_to_minimal() -> None:
    client = _SequenceClient([RuntimeError(_REJECT_VALUE_400)])
    await _create_with_token_param_retry(
        client, {"model": "m", "messages": [], "reasoning_effort": "none"}
    )
    assert client.calls[0]["reasoning_effort"] == "none"
    assert client.calls[1]["reasoning_effort"] == "minimal", (
        "a model that knows the knob but not 'none' must keep the thought "
        "budget capped via 'minimal', not lose the cap entirely"
    )


async def test_parameter_rejection_drops_the_knob() -> None:
    client = _SequenceClient(
        [RuntimeError(_REJECT_VALUE_400), RuntimeError(_REJECT_PARAM_400)]
    )
    await _create_with_token_param_retry(
        client, {"model": "m", "messages": [], "reasoning_effort": "none"}
    )
    assert "reasoning_effort" not in client.calls[2]


# ---------------------------------------------------------------------------
# 3. Old SDKs without the kwarg (TypeError before any request).
# ---------------------------------------------------------------------------


async def test_rejections_are_remembered_per_endpoint_and_model() -> None:
    """A tool loop calls the same endpoint+model dozens of times per mission;
    the rejection round-trips must be paid once, not on every step."""
    first = _SequenceClient(
        [RuntimeError(_REJECT_VALUE_400), RuntimeError(_REJECT_PARAM_400)]
    )
    first.base_url = "https://api.x.ai/v1"
    await _create_with_token_param_retry(
        first, {"model": "m", "messages": [], "reasoning_effort": "none"}
    )
    assert len(first.calls) == 3

    second = _SequenceClient()
    second.base_url = "https://api.x.ai/v1"
    await _create_with_token_param_retry(
        second, {"model": "m", "messages": [], "reasoning_effort": "none"}
    )
    assert len(second.calls) == 1, "remembered adaptations must pre-apply"
    assert "reasoning_effort" not in second.calls[0]


async def test_adaptation_memory_is_scoped_to_the_endpoint() -> None:
    first = _SequenceClient([RuntimeError(_REJECT_PARAM_400)])
    first.base_url = "https://api.x.ai/v1"
    await _create_with_token_param_retry(
        first, {"model": "m", "messages": [], "reasoning_effort": "none"}
    )

    other = _SequenceClient()
    other.base_url = "https://integrate.api.nvidia.com/v1"
    await _create_with_token_param_retry(
        other, {"model": "m", "messages": [], "reasoning_effort": "none"}
    )
    assert other.calls[0]["reasoning_effort"] == "none", (
        "another endpoint's rejection must not strip the knob here"
    )


async def test_sdk_typeerror_strips_reasoning_effort_and_retries() -> None:
    client = _SequenceClient(
        [TypeError("create() got an unexpected keyword argument 'reasoning_effort'")]
    )
    await _drain(client, _req(reasoning_effort="none"))
    assert "reasoning_effort" in client.calls[0]
    assert "reasoning_effort" not in client.calls[1]


# ---------------------------------------------------------------------------
# 4. Gateway extra_body stays extra_body; no double-send.
# ---------------------------------------------------------------------------


async def test_extra_body_is_passed_as_extra_body_not_top_level() -> None:
    client = _SequenceClient()
    body = {"reasoning": {"enabled": False}}
    await _drain(client, _req(reasoning_effort="none"), extra_body=body)
    call = client.calls[0]
    assert call["extra_body"] == body
    assert "reasoning" not in call, (
        "a top-level 'reasoning' kwarg is a guaranteed TypeError on the real "
        "SDK (create() accepts no **kwargs) — the OpenRouter reasoning "
        "opt-out crash this test pins"
    )
    assert "reasoning_effort" not in call, (
        "the native knob must not be double-sent next to a gateway "
        "reasoning directive"
    )
