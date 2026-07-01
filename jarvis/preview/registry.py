"""Preview registry: collects running dev servers for the sidebar preview view.

Events are NOT defined in core/events.py (scope separation) — but they do
inherit from Event so bus subscriptions and the flight recorder treat them
uniformly.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jarvis.core.events import Event

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreviewServerStarted(Event):
    """A Jarvis-Agent started a dev server and registered it."""
    port: int = 0
    title: str = ""
    kind: str = ""  # "vite" | "flask" | "static" | ...
    url: str = ""


@dataclass(frozen=True, slots=True)
class PreviewServerClosed(Event):
    """The dev server was stopped or the Jarvis-Agent has ended."""
    port: int = 0


@dataclass
class PreviewEntry:
    port: int
    title: str
    kind: str
    url: str
    started_ns: int
    agent_trace_id: str | None = None


class PreviewRegistry:
    """Holds a current list of running dev servers.

    Subscribes to ``PreviewServerStarted`` and ``PreviewServerClosed``
    over the bus and updates the internal list.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._entries: dict[int, PreviewEntry] = {}

    def attach(self) -> "PreviewRegistry":
        self._bus.subscribe(PreviewServerStarted, self._on_started)
        self._bus.subscribe(PreviewServerClosed, self._on_closed)
        return self

    def list(self) -> list[PreviewEntry]:
        return list(self._entries.values())

    async def _on_started(self, e: PreviewServerStarted) -> None:
        entry = PreviewEntry(
            port=e.port,
            title=e.title or f"Port {e.port}",
            kind=e.kind or "unknown",
            url=e.url or f"http://localhost:{e.port}",
            started_ns=e.timestamp_ns,
            agent_trace_id=str(e.trace_id) if e.trace_id else None,
        )
        self._entries[e.port] = entry
        log.info("Preview server registered: port=%d title=%r", e.port, e.title)

    async def _on_closed(self, e: PreviewServerClosed) -> None:
        self._entries.pop(e.port, None)
        log.info("Preview server removed: port=%d", e.port)


__all__ = [
    "PreviewServerStarted",
    "PreviewServerClosed",
    "PreviewEntry",
    "PreviewRegistry",
]
