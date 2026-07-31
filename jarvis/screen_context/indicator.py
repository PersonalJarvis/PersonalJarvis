"""Truthful acknowledgement and lifecycle events for the capture indicator.

The EventBus deliberately treats subscriber failures as isolated and a publish
with zero subscribers as success. Screen Context needs a stronger fact before
the shutter: whether the sidecar actually processed a visible ``show`` command.
This tiny turn-local registry bridges that acknowledgement without coupling the
capture service to the Computer-Use indicator implementation.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from uuid import UUID

from jarvis.core.events import Event


@dataclass(frozen=True, slots=True)
class ScreenCaptureIndicatorDismissed(Event):
    """The capture attempt ended; hide the indicator even after a failure."""


_lock = threading.Lock()
_waiters: dict[UUID, asyncio.Future[bool]] = {}


def prepare(trace_id: UUID) -> asyncio.Future[bool]:
    """Create the one acknowledgement slot for a pending capture."""
    waiter = asyncio.get_running_loop().create_future()
    with _lock:
        previous = _waiters.pop(trace_id, None)
        _waiters[trace_id] = waiter
    if previous is not None and not previous.done():
        previous.cancel()
    return waiter


def acknowledge(trace_id: UUID, *, visible: bool) -> bool:
    """Resolve a pending slot after the renderer processed ``show``."""
    with _lock:
        waiter = _waiters.pop(trace_id, None)
    if waiter is None or waiter.done():
        return False
    waiter.set_result(bool(visible))
    return True


def discard(trace_id: UUID) -> None:
    """Remove a timed-out slot without retaining a turn beyond its lifetime."""
    with _lock:
        waiter = _waiters.pop(trace_id, None)
    if waiter is not None and not waiter.done():
        waiter.cancel()


__all__ = [
    "ScreenCaptureIndicatorDismissed",
    "acknowledge",
    "discard",
    "prepare",
]
