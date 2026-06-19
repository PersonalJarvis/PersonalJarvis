"""Tiny test kit (no third-party deps). Only depends on the shared event contract.

`FlightLog` is the wildcard "flight recorder" — exactly the production pattern
where `subscribe_all` records every event for replay/inspection.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable

from optimistic.events import Event


class FlightLog:
    """Records every event published on the bus, in order."""

    def __init__(self, bus) -> None:
        self.events: list[Event] = []
        bus.subscribe_all(self._record)

    async def _record(self, ev: Event) -> None:
        self.events.append(ev)

    def has(self, etype: type) -> bool:
        return any(isinstance(e, etype) for e in self.events)

    def of(self, etype: type) -> list[Event]:
        return [e for e in self.events if isinstance(e, etype)]

    def index(self, etype: type) -> int:
        for i, e in enumerate(self.events):
            if isinstance(e, etype):
                return i
        raise AssertionError(f"{etype.__name__} not found in flight log")


def percentile(values: Iterable[float], p: float) -> float:
    s = sorted(values)
    if not s:
        raise ValueError("percentile() of empty sequence")
    idx = int(round((p / 100.0) * (len(s) - 1)))
    return s[max(0, min(len(s) - 1, idx))]


def run(coro):
    """Run an async scenario without depending on pytest-asyncio (cloud-first: no extra dep)."""
    return asyncio.run(coro)
