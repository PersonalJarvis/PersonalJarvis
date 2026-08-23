"""Coalesced publisher for :class:`~jarvis.core.events.AssistantTextDelta`.

A brain stream hands over text in token-sized pieces, twenty to fifty times a
second. Publishing one bus event per piece would wake every wildcard
subscriber (the session recorder, every WebSocket window, the deck) that
often — measurable on the voice hot path (AP-9) and pointless on screen,
where anything under ~60 ms reads as "live" anyway. This publisher buffers
the pieces and publishes the CUMULATIVE text at most once per
``interval_s``; the final ``flush(done=True)`` publishes whatever is left with
the turn's closing flag.

Pure asyncio, no threads of its own: ``feed`` is synchronous (the brain's
``text_consumer`` callback is), schedules a timer on the loop it was created
on, and is safe to call from another thread (``call_soon_threadsafe``). Every
publish is best-effort — a failing subscriber must never reach the caller,
who is in the middle of producing the answer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from jarvis.core.events import AssistantTextDelta

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

log = logging.getLogger(__name__)

#: Default coalescing window — four to five snapshots per second reads as
#: word-by-word on screen without flooding the bus.
DEFAULT_INTERVAL_S = 0.08


class TextDeltaPublisher:
    """Turn a stream of text pieces into a few cumulative ``AssistantTextDelta`` events."""

    def __init__(
        self,
        bus: EventBus | None,
        *,
        channel: str,
        thread_id: str = "",
        trace_id: UUID | None = None,
        source_layer: str = "",
        interval_s: float = DEFAULT_INTERVAL_S,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._bus = bus
        self._channel = channel
        self._thread_id = thread_id
        self._trace_id = trace_id
        self._source_layer = source_layer or f"text_stream.{channel}"
        self._interval_s = max(0.0, float(interval_s))
        self._loop = loop
        self._text = ""
        self._published = ""
        self._dirty = False
        self._handle: asyncio.TimerHandle | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # Producer side (sync, may be off-loop)
    # ------------------------------------------------------------------

    @property
    def text(self) -> str:
        """Everything fed so far."""
        return self._text

    def feed(self, chunk: str) -> None:
        """Append ``chunk`` and schedule a publish within the interval."""
        if not chunk or self._closed:
            return
        self._text += chunk
        self._mark_dirty()

    def set_text(self, snapshot: str) -> None:
        """Replace the whole text (for producers that already keep a running snapshot)."""
        if self._closed or snapshot == self._text:
            return
        self._text = snapshot
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self._bus is None or self._handle is not None:
            return
        loop = self._resolve_loop()
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._handle = loop.call_later(self._interval_s, self._tick)
        else:
            loop.call_soon_threadsafe(self._arm_on_loop)

    def _arm_on_loop(self) -> None:
        if self._handle is None and not self._closed and self._loop is not None:
            self._handle = self._loop.call_later(self._interval_s, self._tick)

    def _resolve_loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                return None
        return self._loop

    def _tick(self) -> None:
        self._handle = None
        if self._closed or not self._dirty or self._loop is None:
            return
        self._loop.create_task(self._publish(done=False))

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def _publish(self, *, done: bool) -> None:
        self._dirty = False
        if self._bus is None:
            return
        text = self._text
        if not done and text == self._published:
            return
        self._published = text
        kwargs: dict[str, Any] = {
            "source_layer": self._source_layer,
            "text": text,
            "thread_id": self._thread_id,
            "channel": self._channel,
            "done": done,
        }
        if self._trace_id is not None:
            kwargs["trace_id"] = self._trace_id
        try:
            await self._bus.publish(AssistantTextDelta(**kwargs))
        except Exception:  # noqa: BLE001 — a live preview must never break the turn
            log.debug("AssistantTextDelta publish failed", exc_info=True)

    async def flush(self, *, done: bool = True) -> None:
        """Publish what is pending now; ``done=True`` also closes the publisher."""
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        if self._closed:
            return
        if done:
            self._closed = True
            # A turn that never produced a word has nothing to close on screen.
            if not self._text and not self._published:
                return
        await self._publish(done=done)

    def cancel(self) -> None:
        """Drop pending text without a closing event (the turn was aborted)."""
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        self._closed = True
        self._dirty = False
