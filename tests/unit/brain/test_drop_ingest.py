"""``ingest_drop`` — capture dropped content as SILENT context (never a turn).

A drop must NOT trigger a brain turn. ``ingest_drop`` classifies the content and
hands it to ``brain.add_dropped_context``; it does not publish any turn-triggering
event. Jarvis reacts only on the user's next real turn.
"""
from __future__ import annotations

import pytest

from jarvis.brain.drop_context import DroppedItem, ingest_drop


class _FakeBrain:
    def __init__(self) -> None:
        self.dropped: list[tuple] = []

    def add_dropped_context(self, text, images=()) -> None:
        self.dropped.append((text, images))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


@pytest.mark.asyncio
async def test_ingest_drop_adds_context_and_triggers_no_turn() -> None:
    brain = _FakeBrain()
    bus = _FakeBus()
    items = [
        DroppedItem("a.png", "image/png", b"img"),
        DroppedItem("b.txt", "text/plain", b"hello body"),
    ]

    ok = await ingest_drop(bus=bus, brain=brain, thread_id="t1", items=items)

    assert ok is True
    # Captured as context...
    assert len(brain.dropped) == 1
    text, images = brain.dropped[0]
    assert "a.png" in text and "b.txt" in text and "hello body" in text
    assert len(images) == 1
    # ...and NOTHING was published that could trigger a turn.
    assert bus.published == []


@pytest.mark.asyncio
async def test_ingest_empty_drop_captures_nothing() -> None:
    brain = _FakeBrain()
    ok = await ingest_drop(brain=brain, thread_id="t1", items=[])
    assert ok is False
    assert brain.dropped == []


@pytest.mark.asyncio
async def test_ingest_dragged_text_only_is_captured() -> None:
    brain = _FakeBrain()
    ok = await ingest_drop(
        brain=brain, thread_id="t3", items=[], dragged_text="https://example.com/x"
    )
    assert ok is True
    assert "example.com" in brain.dropped[0][0]


@pytest.mark.asyncio
async def test_ingest_without_brain_returns_false() -> None:
    ok = await ingest_drop(
        brain=None, thread_id="t1", items=[DroppedItem("b.txt", "text/plain", b"hi")]
    )
    assert ok is False


@pytest.mark.asyncio
async def test_ingest_text_only_drop_carries_no_images() -> None:
    brain = _FakeBrain()
    await ingest_drop(
        brain=brain, thread_id="t1", items=[DroppedItem("b.txt", "text/plain", b"hello")]
    )
    _text, images = brain.dropped[0]
    assert images == ()
