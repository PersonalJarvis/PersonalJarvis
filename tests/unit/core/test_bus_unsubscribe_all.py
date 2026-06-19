"""Regression guard for the subscribe_all/unsubscribe_all symmetry.

Short-lived wildcard observers (e.g. the Run Inspector live WebSocket, which
exists only while the view is open) must be able to detach from the bus. Before
``unsubscribe_all`` existed, every connect/disconnect left a dead handler in
``_wildcard_subscribers`` and every future event called all of them — a leak
that compounds in the long-lived tray process.
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import Event


@pytest.mark.asyncio
async def test_unsubscribe_all_detaches_wildcard_handler() -> None:
    bus = EventBus()
    seen: list[Event] = []

    async def h(ev: Event) -> None:
        seen.append(ev)

    bus.subscribe_all(h)
    await bus.publish(Event())
    assert len(seen) == 1

    bus.unsubscribe_all(h)
    await bus.publish(Event())
    assert len(seen) == 1  # no new delivery after detach

    bus.unsubscribe_all(h)  # idempotent — absent handler must not raise


@pytest.mark.asyncio
async def test_unsubscribe_all_noop_when_never_subscribed() -> None:
    bus = EventBus()

    async def h(ev: Event) -> None:  # pragma: no cover — never invoked
        pass

    # Detaching a handler that was never registered is a safe no-op.
    bus.unsubscribe_all(h)
    await bus.publish(Event())
