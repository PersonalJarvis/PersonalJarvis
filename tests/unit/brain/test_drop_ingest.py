"""``ingest_drop`` — compose a drop directive, inject images keyed by trace_id,
publish one ``MessageSent`` so the normal reply pipeline reacts proactively.
"""
from __future__ import annotations

import pytest

from jarvis.brain.drop_context import (
    DROP_SOURCE_LAYER,
    DroppedItem,
    ingest_drop,
)
from jarvis.core.events import MessageSent


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


class _FakeBrain:
    def __init__(self) -> None:
        self.injected: list[tuple] = []

    def inject_images_for_turn(self, trace_id, images) -> None:
        self.injected.append((trace_id, images))


@pytest.mark.asyncio
async def test_ingest_drop_publishes_message_and_injects_images() -> None:
    bus = _FakeBus()
    brain = _FakeBrain()
    items = [
        DroppedItem("a.png", "image/png", b"img"),
        DroppedItem("b.txt", "text/plain", b"hello body"),
    ]

    ok = await ingest_drop(bus=bus, brain=brain, thread_id="t1", items=items)

    assert ok is True
    assert len(bus.published) == 1
    msg = bus.published[0]
    assert isinstance(msg, MessageSent)
    assert msg.role == "user"
    assert msg.source_layer == DROP_SOURCE_LAYER
    assert msg.thread_id == "t1"
    assert "a.png" in msg.text and "b.txt" in msg.text and "hello body" in msg.text
    # The image is injected keyed by the SAME trace_id the MessageSent carries.
    assert len(brain.injected) == 1
    inj_trace, inj_images = brain.injected[0]
    assert inj_trace == msg.trace_id
    assert len(inj_images) == 1


@pytest.mark.asyncio
async def test_ingest_empty_drop_publishes_nothing() -> None:
    bus = _FakeBus()
    ok = await ingest_drop(bus=bus, brain=None, thread_id="t1", items=[])
    assert ok is False
    assert bus.published == []


@pytest.mark.asyncio
async def test_ingest_text_drop_without_brain_still_publishes() -> None:
    bus = _FakeBus()
    items = [DroppedItem("b.txt", "text/plain", b"hello")]
    ok = await ingest_drop(bus=bus, brain=None, thread_id="t1", items=items)
    assert ok is True
    assert len(bus.published) == 1
    assert bus.published[0].source_layer == DROP_SOURCE_LAYER


@pytest.mark.asyncio
async def test_ingest_drop_without_images_does_not_inject() -> None:
    bus = _FakeBus()
    brain = _FakeBrain()
    items = [DroppedItem("b.txt", "text/plain", b"hello")]
    await ingest_drop(bus=bus, brain=brain, thread_id="t1", items=items)
    assert brain.injected == []  # nothing to inject for a text-only drop
