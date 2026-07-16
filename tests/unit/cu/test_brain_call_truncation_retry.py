"""Length-truncation recovery in the CU vision dispatch (brain_call.py).

Live forensic 2026-07-16 (voice session 10:37, and every CU mission that
morning): the fast vision model turned thinking-by-default server-side and
spent 304 of the 320 ``max_tokens`` on internal thoughts — the visible reply
arrived as ``{"action": "open_app", "name": "`` and every step died on
"unterminated JSON" until the mission aborted with "could not get a valid
screen-control response". Two defenses, both provider-agnostic:

1. Every CU request carries ``reasoning_effort="none"`` — providers with a
   thinking knob (Gemini) disable internal reasoning for these small
   deterministic JSON calls; providers without one ignore the hint.
2. When a reply still hits the token cap before completing its JSON
   (``is_length_truncated`` + no complete payload), ``_try`` retries ONCE on
   the same provider with real headroom (``max(2048, 4×max_tokens)``) —
   covering thinking models behind gateways that expose no reasoning knob.

Fakes only (no real network) — mirrors the ``_FakeManager``/``_FakeBrain``
shape used in ``test_brain_call_cu_provider.py``.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import BrainDelta, ImageBlock
from jarvis.cu.brain_call import call_vision_brain

_TRUNCATED = '{"action": "open_app", "name": "'
_COMPLETE = '[{"action": "open_app", "name": "Discord"}]'


class _ScriptedBrain:
    """Yields one scripted (text, finish_reason) per call; records requests."""

    supports_tools = False
    supports_vision = True

    def __init__(self, script: list[tuple[str, str | None]]) -> None:
        self.script = list(script)
        self.requests: list[Any] = []

    async def complete(self, req: Any):  # type: ignore[no-untyped-def]
        self.requests.append(req)
        text, finish = (
            self.script.pop(0) if self.script else (_COMPLETE, "stop")
        )
        if text:
            yield BrainDelta(content=text)
        if finish:
            yield BrainDelta(finish_reason=finish)

    def estimate_cost(self, req: Any) -> float:
        return 0.0


class _FakeManager:
    """BrainManager-shaped fake exposing only the CU dispatch surface."""

    active_provider = "brain-primary"

    def __init__(self, brain: _ScriptedBrain) -> None:
        self.brain = brain

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        assert level == "fast"
        return [("brain-primary", None)]

    def _get_brain(self, name: str, model: str | None = None) -> _ScriptedBrain:
        return self.brain

    def _cu_model(self, name: str) -> str | None:
        return None

    def _cu_provider(self) -> str:
        return ""


def _build_prompt(provider: str, brain: Any) -> tuple[str, str]:
    return "system", "user"


_IMG = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")


async def test_truncated_json_reply_retries_once_with_headroom() -> None:
    """A MAX_TOKENS-cut reply with incomplete JSON triggers exactly ONE retry
    on the same provider, with a much larger ceiling — and the retry's clean
    reply is what the caller receives."""
    brain = _ScriptedBrain([
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),
        (_COMPLETE, "stop"),
    ])
    manager = _FakeManager(brain)

    reply = await call_vision_brain(
        manager, build_prompt=_build_prompt, images=[_IMG],
        max_tokens=320, early_stop_json=True,
    )

    assert reply.text == _COMPLETE
    assert len(brain.requests) == 2
    assert brain.requests[0].max_tokens == 320
    assert brain.requests[1].max_tokens == 2048  # max(2048, 4*320)


async def test_complete_json_never_retries() -> None:
    """A clean single-shot reply dispatches exactly one request."""
    brain = _ScriptedBrain([(_COMPLETE, "stop")])
    manager = _FakeManager(brain)

    reply = await call_vision_brain(
        manager, build_prompt=_build_prompt, images=[_IMG],
        max_tokens=320, early_stop_json=True,
    )

    assert reply.text == _COMPLETE
    assert len(brain.requests) == 1


async def test_natural_stop_prose_does_not_retry() -> None:
    """A model that finished on its own terms (finish=stop) without JSON is a
    parse problem for the engine's failure budget, not a token-cap problem —
    no retry burned on it."""
    brain = _ScriptedBrain([("I cannot help with that.", "stop")])
    manager = _FakeManager(brain)

    reply = await call_vision_brain(
        manager, build_prompt=_build_prompt, images=[_IMG],
        max_tokens=320, early_stop_json=True,
    )

    assert reply.text == "I cannot help with that."
    assert len(brain.requests) == 1


async def test_still_truncated_after_retry_returns_text_without_third_call() -> None:
    """The headroom retry is ONE-shot: a second truncated reply is returned
    as-is (the engine counts the parse failure) — never a third dispatch."""
    brain = _ScriptedBrain([
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),
    ])
    manager = _FakeManager(brain)

    reply = await call_vision_brain(
        manager, build_prompt=_build_prompt, images=[_IMG],
        max_tokens=320, early_stop_json=True,
    )

    assert reply.text == _TRUNCATED
    assert len(brain.requests) == 2


async def test_every_cu_request_asks_for_minimal_reasoning() -> None:
    """Both the first attempt and the headroom retry carry
    ``reasoning_effort="none"`` — the provider-side thinking kill-switch."""
    brain = _ScriptedBrain([
        (_TRUNCATED, "FinishReason.MAX_TOKENS"),
        (_COMPLETE, "stop"),
    ])
    manager = _FakeManager(brain)

    await call_vision_brain(
        manager, build_prompt=_build_prompt, images=[_IMG],
        max_tokens=320, early_stop_json=True,
    )

    assert [r.reasoning_effort for r in brain.requests] == ["none", "none"]
