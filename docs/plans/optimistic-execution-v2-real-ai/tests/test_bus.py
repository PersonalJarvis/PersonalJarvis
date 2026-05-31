"""Unit tests for optimistic/bus.py — EventBus.

TDD: written BEFORE bus.py exists. All tests must fail with ImportError /
AttributeError first, then go green once the implementation is in place.

No third-party deps. asyncio.run() inside sync test functions (no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import logging

from optimistic.bus import EventBus
from optimistic.events import (
    AckEmitted,
    Event,
    UserUtterance,
    WorkerStarted,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_bus() -> EventBus:
    return EventBus()


async def _noop(ev: Event) -> None:
    pass


# ---------------------------------------------------------------------------
# Fan-out: typed subscriber and subscribe_all both receive the event
# ---------------------------------------------------------------------------

def test_publish_reaches_typed_subscriber():
    """A handler registered for the exact event type must be called."""
    received: list[Event] = []

    async def _handler(ev: Event) -> None:
        received.append(ev)

    async def _run():
        bus = make_bus()
        bus.subscribe(UserUtterance, _handler)
        ev = UserUtterance(text="Hallo")
        await bus.publish(ev)
        return ev

    ev = asyncio.run(_run())
    assert len(received) == 1
    assert received[0] is ev


def test_publish_reaches_subscribe_all():
    """A subscribe_all handler (wildcard) must receive every event."""
    received: list[Event] = []

    async def _wildcard(ev: Event) -> None:
        received.append(ev)

    async def _run():
        bus = make_bus()
        bus.subscribe_all(_wildcard)
        ev1 = UserUtterance(text="Eins")
        ev2 = AckEmitted(text="Geht klar")
        await bus.publish(ev1)
        await bus.publish(ev2)
        return ev1, ev2

    ev1, ev2 = asyncio.run(_run())
    assert len(received) == 2
    assert received[0] is ev1
    assert received[1] is ev2


def test_publish_fanout_typed_and_wildcard_both_called():
    """Both a typed subscriber and a subscribe_all subscriber must be called."""
    typed_calls: list[Event] = []
    wildcard_calls: list[Event] = []

    async def _typed(ev: Event) -> None:
        typed_calls.append(ev)

    async def _wildcard(ev: Event) -> None:
        wildcard_calls.append(ev)

    async def _run():
        bus = make_bus()
        bus.subscribe(AckEmitted, _typed)
        bus.subscribe_all(_wildcard)
        ev = AckEmitted(text="Mach ich.")
        await bus.publish(ev)
        return ev

    ev = asyncio.run(_run())
    assert len(typed_calls) == 1
    assert typed_calls[0] is ev
    assert len(wildcard_calls) == 1
    assert wildcard_calls[0] is ev


# ---------------------------------------------------------------------------
# Order: typed subscribers before subscribe_all subscribers
# ---------------------------------------------------------------------------

def test_typed_subscribers_called_before_wildcard():
    """Typed handlers run first (registration order), then wildcard handlers."""
    call_order: list[str] = []

    async def _typed(ev: Event) -> None:
        call_order.append("typed")

    async def _wildcard(ev: Event) -> None:
        call_order.append("wildcard")

    async def _run():
        bus = make_bus()
        bus.subscribe(UserUtterance, _typed)
        bus.subscribe_all(_wildcard)
        await bus.publish(UserUtterance(text="test"))

    asyncio.run(_run())
    assert call_order == ["typed", "wildcard"]


# ---------------------------------------------------------------------------
# Raising handler: swallowed — other handlers still run
# ---------------------------------------------------------------------------

def test_raising_handler_swallowed_others_continue():
    """A handler that raises must NOT prevent subsequent handlers from running."""
    after_calls: list[str] = []

    async def _bad_handler(ev: Event) -> None:
        raise RuntimeError("deliberate test error")

    async def _after_handler(ev: Event) -> None:
        after_calls.append("ran")

    async def _run():
        bus = make_bus()
        bus.subscribe(UserUtterance, _bad_handler)
        bus.subscribe(UserUtterance, _after_handler)
        # This must NOT raise:
        await bus.publish(UserUtterance(text="trigger"))

    asyncio.run(_run())
    assert "ran" in after_calls


def test_raising_wildcard_handler_swallowed():
    """A raising subscribe_all handler must not break subsequent handlers."""
    survivor_calls: list[str] = []

    async def _bad(ev: Event) -> None:
        raise ValueError("oops")

    async def _survivor(ev: Event) -> None:
        survivor_calls.append("ok")

    async def _run():
        bus = make_bus()
        bus.subscribe_all(_bad)
        bus.subscribe_all(_survivor)
        await bus.publish(AckEmitted(text="hi"))

    asyncio.run(_run())
    assert survivor_calls == ["ok"]


def test_raising_handler_does_not_propagate_to_publish():
    """publish() itself must not raise even if a handler raises."""
    async def _bad(ev: Event) -> None:
        raise RuntimeError("handler explodes")

    async def _run():
        bus = make_bus()
        bus.subscribe(UserUtterance, _bad)
        # Should return normally:
        await bus.publish(UserUtterance(text="boom"))

    # Must not raise:
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# isinstance semantics: base-class subscription catches subclasses
# ---------------------------------------------------------------------------

def test_subscribe_base_class_catches_subclass():
    """Subscribing to Event (base) must catch UserUtterance, AckEmitted, etc."""
    received: list[Event] = []

    async def _handler(ev: Event) -> None:
        received.append(ev)

    async def _run():
        bus = make_bus()
        bus.subscribe(Event, _handler)
        u = UserUtterance(text="any")
        a = AckEmitted(text="Geht klar")
        await bus.publish(u)
        await bus.publish(a)
        return u, a

    u, a = asyncio.run(_run())
    assert len(received) == 2
    assert received[0] is u
    assert received[1] is a


def test_subscribe_does_not_receive_unrelated_type():
    """A handler subscribed to AckEmitted must NOT be called for UserUtterance."""
    received: list[Event] = []

    async def _handler(ev: Event) -> None:
        received.append(ev)

    async def _run():
        bus = make_bus()
        bus.subscribe(AckEmitted, _handler)
        await bus.publish(UserUtterance(text="hello"))

    asyncio.run(_run())
    assert received == []


# ---------------------------------------------------------------------------
# Multiple typed subscribers — registration order preserved
# ---------------------------------------------------------------------------

def test_multiple_typed_subscribers_all_called_in_order():
    """Multiple subscribers to the same type all run in registration order."""
    log: list[int] = []

    async def _h1(ev: Event) -> None:
        log.append(1)

    async def _h2(ev: Event) -> None:
        log.append(2)

    async def _h3(ev: Event) -> None:
        log.append(3)

    async def _run():
        bus = make_bus()
        bus.subscribe(WorkerStarted, _h1)
        bus.subscribe(WorkerStarted, _h2)
        bus.subscribe(WorkerStarted, _h3)
        await bus.publish(WorkerStarted(mission_id="abc", tool_name="gmail"))

    asyncio.run(_run())
    assert log == [1, 2, 3]


# ---------------------------------------------------------------------------
# Raising handler logs (but does not raise) — logging check
# ---------------------------------------------------------------------------

def test_raising_handler_is_logged(caplog):
    """A failing handler must be logged via 'optimistic.bus' logger."""

    async def _bad(ev: Event) -> None:
        raise TypeError("type error from handler")

    async def _run():
        bus = make_bus()
        bus.subscribe(UserUtterance, _bad)
        await bus.publish(UserUtterance(text="check log"))

    with caplog.at_level(logging.ERROR, logger="optimistic.bus"):
        asyncio.run(_run())

    assert any("type error from handler" in record.message or "TypeError" in record.message
               for record in caplog.records
               if record.name == "optimistic.bus")


# ---------------------------------------------------------------------------
# publish does not mutate or copy the event
# ---------------------------------------------------------------------------

def test_publish_passes_exact_event_object():
    """The handler receives the identical event object (no copy/proxy)."""
    received: list[Event] = []

    async def _handler(ev: Event) -> None:
        received.append(ev)

    orig = UserUtterance(text="identity check")

    async def _run():
        bus = make_bus()
        bus.subscribe(UserUtterance, _handler)
        await bus.publish(orig)

    asyncio.run(_run())
    assert received[0] is orig


# ---------------------------------------------------------------------------
# Empty bus — publish with no subscribers is a no-op (no crash)
# ---------------------------------------------------------------------------

def test_publish_empty_bus_no_crash():
    """publish on a bus with no subscribers must silently succeed."""
    async def _run():
        bus = make_bus()
        await bus.publish(AckEmitted(text="nobody home"))

    asyncio.run(_run())  # must not raise
