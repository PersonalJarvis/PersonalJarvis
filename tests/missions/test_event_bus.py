"""Tests fuer per-Subscriber bounded-queue MissionBus."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.missions.event_bus import MissionBus
from jarvis.missions.events import EventEnvelope, MissionDispatched, now_ms
from jarvis.missions.ids import uuid7_str


def _envelope(prompt: str = "x") -> EventEnvelope:
    return EventEnvelope(
        mission_id=uuid7_str(),
        source_actor="hauptjarvis",
        ts_ms=now_ms(),
        payload=MissionDispatched(prompt=prompt),
    )


async def test_publish_to_one_subscriber() -> None:
    bus = MissionBus(maxsize=8)
    async with bus.subscribe() as sub:
        env = _envelope("hello")
        await bus.publish(env)
        received = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
        assert received.payload.prompt == "hello"  # type: ignore[union-attr]


async def test_publish_to_multiple_subscribers_each_gets_copy() -> None:
    bus = MissionBus(maxsize=8)
    async with bus.subscribe() as sub_a, bus.subscribe() as sub_b:
        await bus.publish(_envelope("broadcast"))
        a = await asyncio.wait_for(sub_a.queue.get(), timeout=1.0)
        b = await asyncio.wait_for(sub_b.queue.get(), timeout=1.0)
        assert a.payload.prompt == "broadcast"  # type: ignore[union-attr]
        assert b.payload.prompt == "broadcast"  # type: ignore[union-attr]


async def test_filter_function_drops_non_matching() -> None:
    bus = MissionBus(maxsize=8)
    only_keep = lambda e: e.payload.prompt == "keep"  # type: ignore[union-attr]  # noqa: E731

    async with bus.subscribe(filter_fn=only_keep) as sub:
        await bus.publish(_envelope("drop"))
        await bus.publish(_envelope("keep"))
        first = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
        assert first.payload.prompt == "keep"  # type: ignore[union-attr]
        # nichts mehr in der Queue
        assert sub.queue.empty()


async def test_drop_oldest_when_subscriber_queue_full() -> None:
    bus = MissionBus(maxsize=2)
    async with bus.subscribe() as sub:
        await bus.publish(_envelope("a"))
        await bus.publish(_envelope("b"))
        await bus.publish(_envelope("c"))  # sollte 'a' rausschmeissen
        # Queue enthaelt jetzt b, c
        first = await sub.queue.get()
        second = await sub.queue.get()
        assert first.payload.prompt == "b"  # type: ignore[union-attr]
        assert second.payload.prompt == "c"  # type: ignore[union-attr]
        assert sub.dropped >= 1


async def test_subscribe_all_wildcard_receives_every_event() -> None:
    bus = MissionBus()
    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    unsub = bus.subscribe_all(handler)
    try:
        await bus.publish(_envelope("a"))
        await bus.publish(_envelope("b"))
    finally:
        unsub()
    assert [e.payload.prompt for e in received] == ["a", "b"]  # type: ignore[union-attr]


async def test_unsubscribe_stops_delivery() -> None:
    bus = MissionBus()
    received: list[EventEnvelope] = []

    async def handler(env: EventEnvelope) -> None:
        received.append(env)

    unsub = bus.subscribe_all(handler)
    await bus.publish(_envelope("first"))
    unsub()
    await bus.publish(_envelope("second"))
    assert len(received) == 1
    assert received[0].payload.prompt == "first"  # type: ignore[union-attr]


async def test_wildcard_handler_error_does_not_break_publish() -> None:
    bus = MissionBus()
    received_good: list[EventEnvelope] = []

    async def bad(_env: EventEnvelope) -> None:
        raise RuntimeError("boom")

    async def good(env: EventEnvelope) -> None:
        received_good.append(env)

    bus.subscribe_all(bad)
    bus.subscribe_all(good)

    await bus.publish(_envelope("x"))  # darf nicht raisen
    assert len(received_good) == 1


async def test_subscription_removed_on_context_exit() -> None:
    bus = MissionBus()
    assert bus.active_subs == 0
    async with bus.subscribe() as _sub:
        assert bus.active_subs == 1
    assert bus.active_subs == 0


async def test_subscription_async_iterator() -> None:
    bus = MissionBus()
    received: list[str] = []

    async def consumer() -> None:
        async with bus.subscribe() as sub:
            async for env in sub:
                received.append(env.payload.prompt)  # type: ignore[union-attr]
                if len(received) >= 2:
                    return

    consumer_task = asyncio.create_task(consumer())
    # kurz warten damit Subscription registriert ist
    await asyncio.sleep(0.01)
    await bus.publish(_envelope("one"))
    await bus.publish(_envelope("two"))
    await asyncio.wait_for(consumer_task, timeout=1.0)
    assert received == ["one", "two"]
