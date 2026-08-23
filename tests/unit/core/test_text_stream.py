"""TextDeltaPublisher: token pieces become a few cumulative AssistantTextDelta events."""

from __future__ import annotations

import asyncio

from jarvis.core.bus import EventBus
from jarvis.core.events import AssistantTextDelta
from jarvis.core.text_stream import TextDeltaPublisher


async def _collect(bus: EventBus) -> list[AssistantTextDelta]:
    seen: list[AssistantTextDelta] = []

    async def _on(event: AssistantTextDelta) -> None:
        seen.append(event)

    bus.subscribe(AssistantTextDelta, _on)
    return seen


async def test_pieces_coalesce_into_cumulative_snapshots() -> None:
    bus = EventBus()
    seen = await _collect(bus)
    pub = TextDeltaPublisher(bus, channel="chat", thread_id="t1", interval_s=0.02)
    for piece in ("Hal", "lo", " Welt", ","):
        pub.feed(piece)
    await asyncio.sleep(0.08)
    pub.feed(" wie geht's?")
    await pub.flush(done=True)

    assert seen, "nothing published"
    # Far fewer events than pieces, every one a growing prefix of the next.
    assert len(seen) <= 3
    texts = [e.text for e in seen]
    for earlier, later in zip(texts, texts[1:], strict=False):
        assert later.startswith(earlier)
    assert seen[-1].text == "Hallo Welt, wie geht's?"
    assert seen[-1].done is True
    assert all(e.thread_id == "t1" and e.channel == "chat" for e in seen)
    assert all(not e.done for e in seen[:-1])


async def test_flush_without_text_publishes_nothing() -> None:
    bus = EventBus()
    seen = await _collect(bus)
    pub = TextDeltaPublisher(bus, channel="voice")
    await pub.flush(done=True)
    assert seen == []
    # Closed: later pieces are ignored.
    pub.feed("late")
    await asyncio.sleep(0.1)
    assert seen == []


async def test_set_text_replaces_the_snapshot() -> None:
    bus = EventBus()
    seen = await _collect(bus)
    pub = TextDeltaPublisher(bus, channel="realtime", interval_s=0.01)
    pub.set_text("Ich")
    pub.set_text("Ich schaue")
    await asyncio.sleep(0.05)
    pub.set_text("Ich schaue nach.")
    await pub.flush(done=True)
    assert seen[-1].text == "Ich schaue nach."
    assert seen[-1].done is True


async def test_cancel_drops_pending_text_silently() -> None:
    bus = EventBus()
    seen = await _collect(bus)
    pub = TextDeltaPublisher(bus, channel="chat", interval_s=0.05)
    pub.feed("half an ans")
    pub.cancel()
    await asyncio.sleep(0.1)
    assert seen == []


async def test_no_bus_is_a_quiet_no_op() -> None:
    pub = TextDeltaPublisher(None, channel="chat")
    pub.feed("x")
    await pub.flush(done=True)
    assert pub.text == "x"
