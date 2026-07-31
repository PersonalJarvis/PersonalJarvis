"""RouterBrain × Screen Context: precedence and fail-closed refusal.

Pins the wiring added when Screen Context landed. The interesting cases are the
ones where the two screen paths meet:

* an explicit look-request must be served by Screen Context (cursor monitor,
  redacted, non-persistent) rather than by permanent vision (foreground window,
  unfiltered, written to the blob store);
* an ambiguous turn must ask AND leave permanent vision shut, or Jarvis attaches
  an image while asking whether it may look at one;
* a privacy refusal must leave permanent vision shut, or the fallback
  photographs the exact window the rule protects;
* a technical failure must also shut alternate screen paths while explaining
  the host limitation honestly.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest

from jarvis.brain.router import RouterBrain
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, BrainTierConfig, JarvisConfig
from jarvis.core.protocols import (
    BrainDelta,
    BrainRequest,
    ImageBlock,
    Observation,
    ToolResult,
)
from jarvis.screen_context.turn import TurnScreenContext


class _FakeBrain:
    name = "fake"
    context_window = 8192
    supports_tools = True
    supports_vision = True
    model = "fake-model"

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tool_overrides: list[dict[str, Any] | None] = []

    def tools_payload(self) -> list[dict[str, Any]]:
        return []

    async def dispatch(
        self,
        user_text: str,
        *,
        images: tuple[ImageBlock, ...] = (),
        history: Any = None,
        trace_id: UUID | None = None,
        ack_emitter: Any = None,
        turn_context: str = "",
        **_kwargs: Any,
    ) -> StreamingAggregate:
        self.calls.append(
            {"user_text": user_text, "images": images, "turn_context": turn_context}
        )
        agg = StreamingAggregate()
        agg.text = "ok"
        agg.finish_reason = "stop"
        return agg


class _FakeTool:
    name = "bash"
    description = "Run shell commands."
    risk_tier = "monitor"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _FakeVisionProvider:
    """Stands in for permanent vision; records whether it was consulted."""

    is_paused = False

    def __init__(self) -> None:
        self.consulted = 0

    async def current(self) -> Observation:
        self.consulted += 1
        raise RuntimeError("permanent vision must not be reached in these cases")


def _build_router_config() -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.providers["fake"] = BrainProviderConfig(
        model="fake-model", deep_model="fake-model"
    )
    cfg.brain.router = BrainTierConfig(
        provider="fake",
        model="fake-model",
        fallback_provider="fake",
        fallback_model="fake-model",
    )
    cfg.brain.worker = BrainTierConfig(provider="fake", model="fake-model")
    return cfg


class _NoopToolExecutor:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return await tool.execute(args, ctx=None)


def _build_router(vision_provider: Any = None) -> tuple[RouterBrain, _RecordingDispatcher]:
    router = RouterBrain(
        _build_router_config(),
        EventBus(),
        tools={"bash": _FakeTool()},
        tool_executor=_NoopToolExecutor(),
        vision_provider=vision_provider,
    )
    router.manager._brain_cache[("fake", "fake-model")] = _FakeBrain()
    recorder = _RecordingDispatcher()
    def _dispatcher(_brain: Any, *, tools_override=None, **_kwargs: Any):
        recorder.tool_overrides.append(tools_override)
        return recorder

    router.manager._build_dispatcher = _dispatcher  # type: ignore[method-assign]
    return router, recorder


def _patch_screen(monkeypatch: pytest.MonkeyPatch, result: TurnScreenContext) -> None:
    async def _fake(*_args: Any, **_kwargs: Any) -> TurnScreenContext:
        return result

    monkeypatch.setattr("jarvis.screen_context.turn.screen_context_for_turn", _fake)


async def test_captured_context_is_attached_with_its_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(
            status="captured",
            image=b"jpeg",
            mime="image/jpeg",
            note="Screen capture of monitor 2.",
            receipt="captured monitor 2",
            source_hash="abc",
        ),
    )
    router, recorder = _build_router()

    [d async for d in router.handle("look at this")]

    call = recorder.calls[0]
    assert len(call["images"]) == 1
    assert call["images"][0].mime == "image/jpeg"
    assert call["turn_context"] == "Screen capture of monitor 2."
    assert call["user_text"] == "look at this", (
        "the raw utterance must reach the gates unchanged — the note travels "
        "in turn_context precisely so it cannot widen cu_gate/spawn_gate"
    )
    assert recorder.tool_overrides == [{}], (
        "a read-only screen turn must expose no Computer-Use or action tools"
    )


async def test_screen_context_wins_over_permanent_vision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The better path serves the request; the old one is not also consulted."""
    _patch_screen(
        monkeypatch,
        TurnScreenContext(
            status="captured", image=b"jpeg", mime="image/jpeg", note="note"
        ),
    )
    provider = _FakeVisionProvider()
    router, recorder = _build_router(vision_provider=provider)

    [d async for d in router.handle("look at this")]

    assert provider.consulted == 0
    assert len(recorder.calls[0]["images"]) == 1


async def test_ambiguous_turn_asks_and_never_calls_the_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(status="clarify", question="Shall I take a look?"),
    )
    provider = _FakeVisionProvider()
    router, recorder = _build_router(vision_provider=provider)

    deltas = [d async for d in router.handle("what is that?")]

    spoken = "".join(d.content or "" for d in deltas)
    assert spoken == "Shall I take a look?"
    assert recorder.calls == [], "an ambiguous turn must not reach the brain"
    assert provider.consulted == 0, (
        "attaching an image while asking whether to look is the exact outcome "
        "the clarify path exists to prevent"
    )
    assert deltas[-1].finish_reason == "stop"


async def test_privacy_refusal_is_spoken_and_shuts_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(
            status="refused", message="Your privacy rule blocked that window."
        ),
    )
    provider = _FakeVisionProvider()
    router, recorder = _build_router(vision_provider=provider)

    deltas = [d async for d in router.handle("look at this")]

    assert "privacy rule" in "".join(d.content or "" for d in deltas)
    assert recorder.calls == []
    assert provider.consulted == 0, (
        "falling back here would capture the very window the rule protects"
    )


async def test_technical_failure_ends_honestly_without_computer_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless hosts degrade honestly and never turn looking into acting."""
    _patch_screen(
        monkeypatch,
        TurnScreenContext(status="unavailable", message="No display available."),
    )
    provider = _FakeVisionProvider()
    router, recorder = _build_router(vision_provider=provider)

    deltas = [d async for d in router.handle("look at this")]

    assert recorder.calls == []
    assert provider.consulted == 0
    assert "No display" in "".join(d.content or "" for d in deltas)


async def test_a_plain_turn_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_screen(monkeypatch, TurnScreenContext(status="none"))
    router, recorder = _build_router()

    [d async for d in router.handle("what did we discuss?")]

    call = recorder.calls[0]
    assert call["images"] == ()
    assert call["turn_context"] == ""
