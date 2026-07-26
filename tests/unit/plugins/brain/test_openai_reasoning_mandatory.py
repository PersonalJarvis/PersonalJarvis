"""Recovery from an endpoint that REQUIRES internal reasoning.

Field report (2026-07-26, delegated realtime voice turn): every turn opened
with two HTTP 400s from OpenRouter's ``google/gemini-3.5-flash`` —

    Reasoning is mandatory for this endpoint and cannot be disabled.

— after which the provider dropped out of the chain entirely and the turn fell
through to the next family. Two tool-loop rounds of that took 41 s against a
20 s deadline, and the user heard "the search took a bit long" instead of an
answer.

The complaint is NOT "unsupported parameter": the endpoint understands the
knob and refuses its OFF value, so none of the ``unsupported``-marker
degradation paths in ``_compatible_retry_kwargs`` match it. It also arrives
for BOTH spellings of "off" — OpenAI's native ``reasoning_effort="none"`` and
a gateway's ``reasoning={"enabled": False}`` — which is why the pre-existing
fail-open retry in the OpenRouter plugin could not save it either: dropping
the gateway object let this base layer re-add the native parameter, earning
the identical 400.

Contract under test:
  1. A mandatory-reasoning rejection retries with BOTH opt-outs removed, in
     ONE step, and the call streams.
  2. The adaptation is remembered per (endpoint, model), so a tool loop pays
     the rejection once rather than on every round.
  3. A request that never asked to disable reasoning is left alone, and
     unrelated errors still propagate — this must not become a blanket retry.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.plugins.brain import _openai_base
from jarvis.plugins.brain._openai_base import _create_with_token_param_retry

#: The verbatim upstream complaint. It carries no ``param``/``code`` metadata
#: and never spells the parameter name, so both the structured and the
#: substring rejection paths are blind to it by construction.
_MANDATORY_400 = (
    "Error code: 400 - {'error': {'message': 'Reasoning is mandatory for "
    "this endpoint and cannot be disabled.', 'code': 400}}"
)

_GATEWAY_OFF = {"reasoning": {"enabled": False}}


@pytest.fixture(autouse=True)
def _fresh_adaptation_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rejected-param memory is process-global; isolate it per test."""
    monkeypatch.setattr(_openai_base, "_PARAM_ADAPTATION_CACHE", {})


class _EmptyStream:
    def __aiter__(self) -> _EmptyStream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _SequenceClient:
    """create() raises the queued errors first, then returns an empty stream."""

    def __init__(
        self, errors: list[Exception] | None = None, *, base_url: str = "https://gw/api"
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.base_url = base_url
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


def _kwargs(**extra: Any) -> dict[str, Any]:
    return {"model": "google/gemini-3.5-flash", "messages": [], **extra}


# ---------------------------------------------------------------------------
# 1. One retry clears BOTH opt-outs.
# ---------------------------------------------------------------------------


async def test_mandatory_reasoning_drops_the_native_knob() -> None:
    client = _SequenceClient([RuntimeError(_MANDATORY_400)])

    await _create_with_token_param_retry(
        client, _kwargs(reasoning_effort="none")
    )

    assert client.calls[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in client.calls[1], (
        "an endpoint that refuses to disable reasoning must lose the OFF "
        "knob, not be retried with it"
    )


async def test_mandatory_reasoning_drops_the_gateway_directive() -> None:
    client = _SequenceClient([RuntimeError(_MANDATORY_400)])

    await _create_with_token_param_retry(
        client, _kwargs(extra_body=dict(_GATEWAY_OFF))
    )

    assert client.calls[0]["extra_body"] == _GATEWAY_OFF
    assert "reasoning" not in client.calls[1].get("extra_body", {})


async def test_both_opt_outs_are_cleared_in_a_single_retry() -> None:
    """The two spellings must not cost one rejection round-trip each.

    Removing only one re-sends the other and earns the identical 400 — the
    exact loop that made a live turn spend two 400s before moving on.
    """
    client = _SequenceClient([RuntimeError(_MANDATORY_400)])

    await _create_with_token_param_retry(
        client,
        _kwargs(reasoning_effort="none", extra_body=dict(_GATEWAY_OFF)),
    )

    assert len(client.calls) == 2, "one rejection, one recovery — no second 400"
    recovered = client.calls[1]
    assert "reasoning_effort" not in recovered
    assert "reasoning" not in recovered.get("extra_body", {})


async def test_unrelated_extra_body_entries_survive_the_retry() -> None:
    client = _SequenceClient([RuntimeError(_MANDATORY_400)])

    await _create_with_token_param_retry(
        client,
        _kwargs(extra_body={**_GATEWAY_OFF, "transforms": ["middle-out"]}),
    )

    assert client.calls[1]["extra_body"] == {"transforms": ["middle-out"]}


# ---------------------------------------------------------------------------
# 2. Remembered per endpoint — a tool loop pays it once.
# ---------------------------------------------------------------------------


async def test_the_endpoint_is_remembered_so_later_rounds_skip_the_400() -> None:
    first = _SequenceClient([RuntimeError(_MANDATORY_400)])
    await _create_with_token_param_retry(
        first, _kwargs(reasoning_effort="none", extra_body=dict(_GATEWAY_OFF))
    )

    # Same endpoint + model, fresh client: a tool loop's next round.
    second = _SequenceClient()
    await _create_with_token_param_retry(
        second, _kwargs(reasoning_effort="none", extra_body=dict(_GATEWAY_OFF))
    )

    assert len(second.calls) == 1, "the remembered endpoint must not re-probe"
    assert "reasoning_effort" not in second.calls[0]
    assert "reasoning" not in second.calls[0].get("extra_body", {})


async def test_a_different_model_still_gets_its_own_chance() -> None:
    """The memory is keyed by (endpoint, model) — never a blanket rule."""
    first = _SequenceClient([RuntimeError(_MANDATORY_400)])
    await _create_with_token_param_retry(first, _kwargs(reasoning_effort="none"))

    other = _SequenceClient()
    await _create_with_token_param_retry(
        other, {"model": "openai/gpt-5.5", "messages": [], "reasoning_effort": "none"}
    )

    assert other.calls[0]["reasoning_effort"] == "none"


# ---------------------------------------------------------------------------
# 3. Narrow by construction.
# ---------------------------------------------------------------------------


async def test_unrelated_error_propagates_unchanged() -> None:
    client = _SequenceClient([RuntimeError("Error code: 401 - invalid api key")])

    with pytest.raises(RuntimeError, match="invalid api key"):
        await _create_with_token_param_retry(
            client, _kwargs(reasoning_effort="none")
        )


async def test_a_request_that_never_disabled_reasoning_is_not_retried() -> None:
    """"medium" is not an opt-out; there is nothing here to give up."""
    client = _SequenceClient([RuntimeError(_MANDATORY_400)])

    with pytest.raises(RuntimeError, match="mandatory"):
        await _create_with_token_param_retry(
            client, _kwargs(reasoning_effort="medium")
        )
