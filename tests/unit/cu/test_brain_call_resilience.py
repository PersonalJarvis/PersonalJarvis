"""Two latency/availability defenses in the CU vision dispatch.

Both were written from one live mission (2026-08-10 18:39-18:43, voice goal
"open Elon's post"), where Computer-Use first announced "I can't see the screen
right now" and then crawled at ~9 s per step:

1. **Transient failures no longer blind the mission.** Gemini answered
   ``503 … This model is currently experiencing high demand``. The chain treated
   that exactly like a broken credential: it moved on to OpenRouter (402, no
   credits) and OpenAI (429, no credits) and gave up — while the very same
   Gemini key served a full mission three minutes later. A capacity blip now
   earns ONE in-place retry, and a single blip no longer blocks the candidate
   for the rest of the mission. Credential/quota failures are explicitly
   excluded so the honest "check your keys/credit" readback survives.

2. **The token headroom is learned once, not per step.** Every single step
   logged "hit the 320-token cap … retrying once with 2048" — the first reply
   was always discarded, costing a full round-trip per decision. The
   (provider, model) pair is now remembered and the next call starts at the
   headroom.

Fakes only (no real network) — same ``_ScriptedBrain``/``_FakeManager`` shape as
``test_brain_call_truncation_retry.py``.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.protocols import BrainDelta, ImageBlock
from jarvis.cu.brain_call import CUNoVisionProviderError, call_vision_brain

_TRUNCATED = '{"action": "open_app", "name": "'
_COMPLETE = '[{"action": "open_app", "name": "Discord"}]'

_OVERLOADED = (
    "503 Service Unavailable. {'message': '{\\n \"error\": {\\n \"code\": 503,"
    "\\n \"message\": \"This model is currently experiencing high demand. "
    "Spikes in demand are usually temporary. Please try again later.\",\\n "
    "\"status\": \"UNAVAILABLE\"\\n }\\n}\\n', 'status': 'Service Unavailable'}"
)
_NO_CREDITS = (
    "Error code: 429 - {'error': {'message': 'You have no credits remaining. "
    "Add credits to continue using the API.', 'type': 'insufficient_quota', "
    "'code': 'credit_balance_exhausted'}}"
)
_BAD_KEY = (
    "Error code: 401 - {'type': 'error', 'error': {'type': "
    "'authentication_error', 'message': 'invalid x-api-key'}}"
)


class _ScriptedBrain:
    """Yields one scripted step per call: a reply tuple, or an Exception."""

    supports_tools = False
    supports_vision = True

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[Any] = []

    async def complete(self, req: Any):  # type: ignore[no-untyped-def]
        self.requests.append(req)
        step = self.script.pop(0) if self.script else (_COMPLETE, "stop")
        if isinstance(step, Exception):
            raise step
        text, finish = step
        if text:
            yield BrainDelta(content=text)
        if finish:
            yield BrainDelta(finish_reason=finish)

    def estimate_cost(self, req: Any) -> float:
        return 0.0


class _FakeManager:
    """BrainManager-shaped fake with an ordered multi-provider chain."""

    active_provider = "alpha"

    def __init__(self, brains: dict[str, _ScriptedBrain]) -> None:
        self.brains = brains
        self._dead_providers: set[str] = set()

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        assert level == "fast"
        return [(name, "m") for name in self.brains]

    def _get_brain(self, name: str, model: str | None = None) -> _ScriptedBrain:
        return self.brains[name]

    def _cu_model(self, name: str) -> str | None:
        return None

    def _cu_provider(self) -> str:
        return ""


def _build_prompt(provider: str, brain: Any) -> tuple[str, str]:
    return "system", "user"


_IMG = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The transient retry waits 0.6 s in production — not in the suite."""
    import jarvis.cu.brain_call as bc

    monkeypatch.setattr(bc, "_TRANSIENT_RETRY_DELAY_S", 0.0)


async def _call(manager: _FakeManager, **kw: Any):
    return await call_vision_brain(
        manager, build_prompt=_build_prompt, images=[_IMG],
        max_tokens=320, early_stop_json=True, **kw,
    )


# ----------------------------------------------------------------------
# 1. Transient failures
# ----------------------------------------------------------------------


async def test_overloaded_provider_gets_one_in_place_retry() -> None:
    """A 503 is a busy server, not a broken key — the SAME provider is asked
    again instead of the chain falling through to an out-of-credit sibling."""
    alpha = _ScriptedBrain([RuntimeError(_OVERLOADED), (_COMPLETE, "stop")])
    beta = _ScriptedBrain([RuntimeError(_NO_CREDITS)])
    manager = _FakeManager({"alpha": alpha, "beta": beta})

    reply = await _call(manager)

    assert reply.provider == "alpha"
    assert reply.text == _COMPLETE
    assert len(alpha.requests) == 2
    assert not beta.requests  # never reached


async def test_persistent_outage_still_falls_through_to_the_next_provider() -> None:
    """The retry is ONE-shot: a provider that is genuinely down does not trap
    the mission — the chain continues to a healthy brain."""
    alpha = _ScriptedBrain([RuntimeError(_OVERLOADED), RuntimeError(_OVERLOADED)])
    beta = _ScriptedBrain([(_COMPLETE, "stop")])
    manager = _FakeManager({"alpha": alpha, "beta": beta})

    reply = await _call(manager)

    assert reply.provider == "beta"
    assert len(alpha.requests) == 2


@pytest.mark.parametrize("detail", [_NO_CREDITS, _BAD_KEY])
async def test_account_failures_are_never_retried_in_place(detail: str) -> None:
    """No credits / invalid key must fall through IMMEDIATELY. Retrying them
    would burn a round-trip per step and delay the honest credential readback."""
    alpha = _ScriptedBrain([RuntimeError(detail)])
    beta = _ScriptedBrain([(_COMPLETE, "stop")])
    manager = _FakeManager({"alpha": alpha, "beta": beta})

    reply = await _call(manager)

    assert reply.provider == "beta"
    assert len(alpha.requests) == 1


async def test_exhausted_chain_still_raises_the_no_provider_error() -> None:
    """The retry must not swallow a genuinely dead chain — the engine maps this
    exception to exit 3 ("no eyes — check your keys/credit")."""
    alpha = _ScriptedBrain([RuntimeError(_BAD_KEY)])
    beta = _ScriptedBrain([RuntimeError(_NO_CREDITS)])
    manager = _FakeManager({"alpha": alpha, "beta": beta})

    with pytest.raises(CUNoVisionProviderError):
        await _call(manager)


async def test_one_blip_does_not_block_the_candidate_for_the_mission() -> None:
    """The selector's mission-block is what made a 30-second spike cost the
    whole mission. A first transient failure keeps the candidate available."""
    from jarvis.harness.computer_use_planner import ComputerUsePlannerSelector

    selector = ComputerUsePlannerSelector(manager=object(), chain=[("alpha", "m")])
    selector.record_failure("alpha", "m", RuntimeError(_OVERLOADED))
    assert ("alpha", "m") not in selector.mission_blocked

    selector.record_failure("alpha", "m", RuntimeError(_OVERLOADED))
    assert ("alpha", "m") in selector.mission_blocked  # a real outage falls out


async def test_account_failure_blocks_or_dead_lists_immediately() -> None:
    """A terminal credential state still leaves the chain on the FIRST hit."""
    from jarvis.harness.computer_use_planner import ComputerUsePlannerSelector

    class _Mgr:
        _dead_providers: set[str] = set()

    manager = _Mgr()
    selector = ComputerUsePlannerSelector(manager=manager, chain=[("alpha", "m")])
    selector.record_failure("alpha", "m", RuntimeError(_BAD_KEY))
    assert "alpha" in manager._dead_providers


# ----------------------------------------------------------------------
# 2. Learned token headroom
# ----------------------------------------------------------------------


async def test_second_call_starts_at_the_learned_headroom() -> None:
    """After one truncation, the next call skips the doomed small-cap attempt —
    that discarded round-trip was ~a third of every step's latency."""
    brain = _ScriptedBrain([
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),  # call 1, attempt 1
        (_COMPLETE, "stop"),                      # call 1, headroom retry
        (_COMPLETE, "stop"),                      # call 2, straight to headroom
    ])
    manager = _FakeManager({"alpha": brain})

    await _call(manager)
    await _call(manager)

    assert [r.max_tokens for r in brain.requests] == [320, 2048, 2048]


async def test_headroom_is_remembered_per_model_not_globally() -> None:
    """A different model keeps the cheap small cap — the truncation is a
    property of the model, not of Computer-Use."""
    import jarvis.cu.brain_call as bc

    brain = _ScriptedBrain([
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),
        (_COMPLETE, "stop"),
    ])
    manager = _FakeManager({"alpha": brain})
    await _call(manager)

    assert ("alpha", "m") in bc._NEEDS_HEADROOM
    assert ("alpha", "other-model") not in bc._NEEDS_HEADROOM


async def test_learned_headroom_never_costs_a_second_retry() -> None:
    """A call that already STARTS at the headroom and is still truncated is
    returned as-is — the engine counts the parse failure. No third dispatch."""
    brain = _ScriptedBrain([
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),
    ])
    manager = _FakeManager({"alpha": brain})

    await _call(manager)          # 2 requests: 320 then 2048
    reply = await _call(manager)  # 1 request: straight to 2048, no retry

    assert reply.text == _TRUNCATED
    assert [r.max_tokens for r in brain.requests] == [320, 2048, 2048]
