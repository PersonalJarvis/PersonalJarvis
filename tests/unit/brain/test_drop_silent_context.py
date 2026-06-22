"""Dropped content is SILENT context, never an immediate brain turn.

The user's contract: dragging something onto the bar/mascot must NOT make Jarvis
think right away — it is remembered and used on the NEXT time the user actually
speaks/types (the normal conversation flow). A drop while idle is kept for next
time; a drop mid-conversation joins the running context. Either way: no turn is
triggered by the drop itself.
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

    async def dispatch(self, user_text, *, images=(), history=None, **_kwargs):
        self.calls.append({"user_text": user_text, "images": images, "history": history})
        agg = StreamingAggregate()
        agg.text = "reply"
        agg.finish_reason = "stop"
        return agg


def _manager() -> tuple[BrainManager, _RecordingDispatcher]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    m = BrainManager(config=cfg, bus=EventBus(), tools={})
    rec = _RecordingDispatcher()
    m._build_fallback_chain = lambda _l: [("fake", "fake-model")]  # type: ignore[method-assign]
    m._get_brain = lambda _n, _mo: _FakeBrain()  # type: ignore[method-assign]
    m._build_dispatcher = lambda _b, *, tools_override=None: rec  # type: ignore[method-assign]
    return m, rec


_IMG = ImageBlock(mime="image/png", data_b64="ZmFrZQ==")


@pytest.mark.asyncio
async def test_drop_does_not_trigger_a_turn() -> None:
    m, rec = _manager()
    m.add_dropped_context("[dropped report.pdf: SECRET_TOKEN_99]", (_IMG,))
    # The drop alone must NOT have generated anything.
    assert rec.calls == []


@pytest.mark.asyncio
async def test_dropped_text_is_in_context_on_next_turn() -> None:
    m, rec = _manager()
    m.add_dropped_context("[dropped report.pdf: SECRET_TOKEN_99]", ())

    await m.generate("summarize this", trace_id=uuid4(), use_history=True)

    assert len(rec.calls) == 1
    hist = rec.calls[0]["history"] or []
    joined = " ".join(str(getattr(msg, "content", "")) for msg in hist)
    assert "SECRET_TOKEN_99" in joined, "the dropped text must be in the turn's context"


@pytest.mark.asyncio
async def test_dropped_image_reaches_next_turn_only() -> None:
    m, rec = _manager()
    m.add_dropped_context("[dropped photo.png]", (_IMG,))

    await m.generate("what is this", trace_id=uuid4(), use_history=True)
    await m.generate("and now", trace_id=uuid4(), use_history=True)

    assert _IMG in rec.calls[0]["images"]      # consumed on the first real turn
    assert _IMG not in rec.calls[1]["images"]  # not re-sent on the next turn


@pytest.mark.asyncio
async def test_multiple_drops_accumulate() -> None:
    m, rec = _manager()
    m.add_dropped_context("[dropped a.txt: AAA]", ())
    m.add_dropped_context("[dropped b.txt: BBB]", ())

    await m.generate("go", trace_id=uuid4(), use_history=True)

    hist = " ".join(str(getattr(msg, "content", "")) for msg in (rec.calls[0]["history"] or []))
    assert "AAA" in hist and "BBB" in hist
