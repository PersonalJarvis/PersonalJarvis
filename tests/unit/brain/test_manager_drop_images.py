"""Per-turn ad-hoc image injection for the drag-drop feature.

A dropped image must reach the multimodal brain for ONE specific turn, keyed by
that turn's trace_id, bypassing the screen-vision gate (so it works even with
vision off / no vision provider) and clearing after the turn so it never carries
over. See docs/superpowers/specs/2026-06-21-dragdrop-files-into-context-design.md.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainDelta, BrainRequest, ImageBlock


class _FakeBrain:
    name = "fake"
    context_window = 8192
    supports_tools = True
    supports_vision = True

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch(self, user_text, *, images=(), trace_id=None, **_kwargs):
        self.calls.append({"images": images, "trace_id": trace_id})
        agg = StreamingAggregate()
        agg.text = "reacting to the drop"
        agg.finish_reason = "stop"
        return agg


def _manager_with_recorder() -> tuple[BrainManager, _RecordingDispatcher]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    manager = BrainManager(config=cfg, bus=EventBus(), tools={})
    recorder = _RecordingDispatcher()
    manager._build_fallback_chain = lambda _level: [("fake", "fake-model")]  # type: ignore[method-assign]
    manager._get_brain = lambda _name, _model: _FakeBrain()  # type: ignore[method-assign]
    manager._build_dispatcher = lambda _brain, *, tools_override=None, **_kw: recorder  # type: ignore[method-assign]
    return manager, recorder


_IMG = ImageBlock(mime="image/png", data_b64="ZmFrZQ==")  # "fake"


@pytest.mark.asyncio
async def test_injected_images_reach_dispatcher_without_vision_provider() -> None:
    manager, recorder = _manager_with_recorder()
    # No vision provider at all → the normal screen-vision path returns ().
    assert getattr(manager, "_vision_provider", None) is None

    trace = uuid4()
    manager.inject_images_for_turn(trace, (_IMG,))
    await manager.generate("react to this", trace_id=trace, use_history=False)

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["images"] == (_IMG,)


@pytest.mark.asyncio
async def test_injected_images_cleared_after_one_turn() -> None:
    manager, recorder = _manager_with_recorder()
    trace = uuid4()
    manager.inject_images_for_turn(trace, (_IMG,))

    await manager.generate("first", trace_id=trace, use_history=False)
    await manager.generate("second", trace_id=trace, use_history=False)

    assert recorder.calls[0]["images"] == (_IMG,)
    assert recorder.calls[1]["images"] == ()  # buffer consumed, not carried over


@pytest.mark.asyncio
async def test_no_injection_leaves_images_empty() -> None:
    manager, recorder = _manager_with_recorder()
    await manager.generate("plain turn", trace_id=uuid4(), use_history=False)
    assert recorder.calls[0]["images"] == ()


# --- Direct _collect_vision_images seam tests (no dispatcher) ----------------


def _bare_manager() -> BrainManager:
    """A manager built via __new__ (no __init__) — the minimal surface
    _collect_vision_images touches with screen-vision off."""
    mgr = BrainManager.__new__(BrainManager)
    mgr._vision_provider = None
    mgr._config = object()
    mgr._bus = None
    mgr._active_name = "x"
    return mgr


@pytest.mark.asyncio
async def test_collect_pops_injected_and_clears() -> None:
    mgr = _bare_manager()
    trace = uuid4()
    mgr.inject_images_for_turn(trace, (_IMG,))

    first = await mgr._collect_vision_images(trace_id=trace, user_text="hi")
    second = await mgr._collect_vision_images(trace_id=trace, user_text="hi")

    assert first == (_IMG,)  # injected images bypass the (absent) vision provider
    assert second == ()  # popped once, not carried over


@pytest.mark.asyncio
async def test_collect_different_trace_does_not_consume() -> None:
    mgr = _bare_manager()
    injected_trace, other_trace = uuid4(), uuid4()
    mgr.inject_images_for_turn(injected_trace, (_IMG,))

    # A different turn must not consume another turn's injected images.
    assert await mgr._collect_vision_images(trace_id=other_trace) == ()
    # The injected entry is still there for its own trace.
    assert await mgr._collect_vision_images(trace_id=injected_trace) == (_IMG,)


def test_inject_on_new_constructed_manager_does_not_raise() -> None:
    mgr = BrainManager.__new__(BrainManager)  # no __init__ → no buffer attr
    mgr.inject_images_for_turn(uuid4(), (_IMG,))  # defensive guard creates it
    assert mgr._pending_turn_images  # populated, no AttributeError
