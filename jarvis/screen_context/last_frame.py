"""The deck's picture of the last capture — one frame, briefly, then gone.

``ScreenContextService`` is built on "one capture, then gone": a finished
capture lives in a single-use handle and is removed on first consume or on
TTL. That contract is right for the *model's* copy of the screen and stays
untouched here.

This module is a second, deliberately narrower promise for the *user's* own
eyes: the mission deck shows the picture Jarvis just took, so the person at
the keyboard can see what was looked at (maintainer decision 2026-08-17). It
holds AT MOST ONE frame, only in memory, and drops it after
``ScreenContextSettings.deck_preview_s`` seconds — the same budget as an
unconsumed handle by default. There is no history, no disk, and a TTL of
``0`` switches the mirror off entirely.

Nothing here captures anything. The only way a frame arrives is the service
handing over the bytes it already produced for a capture the user asked for.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LastFrame:
    """One captured picture with the metadata the deck shows next to it."""

    #: Monotonic per-process counter — the deck refetches when it changes.
    seq: int
    image: bytes
    mime: str
    width: int
    height: int
    #: Which path produced it (``"screen_context"`` today; kept as a string so
    #: a second producer does not need a schema change).
    source: str
    #: What was captured, already scrubbed by the service (window title or
    #: monitor label). Never raw text from the screen.
    target_label: str
    trace_id: str
    captured_at_ns: int
    expires_at_ns: int


class LastFrameMirror:
    """Thread-safe holder for the most recent frame, with a hard TTL.

    ``get`` is the only reader and it enforces expiry itself, so a frame that
    outlived its budget is dropped on the next look rather than by a timer —
    there is no background task to leak, and a process that never asks again
    simply keeps one stale byte string until the next ``set`` replaces it.
    """

    def __init__(
        self,
        *,
        ttl_s: float = 120.0,
        clock=time.time_ns,
    ) -> None:
        self._ttl_s = max(0.0, float(ttl_s))
        self._clock = clock
        self._lock = threading.Lock()
        self._frame: LastFrame | None = None
        self._seq = 0

    @property
    def ttl_s(self) -> float:
        return self._ttl_s

    @property
    def enabled(self) -> bool:
        return self._ttl_s > 0

    def set_ttl(self, ttl_s: float) -> None:
        """Adopt a new budget; ``0`` switches the mirror off and clears it."""
        with self._lock:
            self._ttl_s = max(0.0, float(ttl_s))
            if self._ttl_s == 0:
                self._frame = None

    def set(
        self,
        image: bytes,
        *,
        mime: str,
        width: int,
        height: int,
        source: str,
        target_label: str = "",
        trace_id: str = "",
    ) -> int:
        """Replace the held frame. Returns the new ``seq``, or 0 when off.

        A disabled mirror stores nothing and returns 0 rather than raising:
        the service calls this on the capture path, and a preference must not
        turn into an exception there.
        """
        with self._lock:
            if self._ttl_s <= 0:
                return 0
            now = self._clock()
            self._seq += 1
            self._frame = LastFrame(
                seq=self._seq,
                image=bytes(image),
                mime=mime,
                width=int(width),
                height=int(height),
                source=source,
                target_label=target_label,
                trace_id=trace_id,
                captured_at_ns=now,
                expires_at_ns=now + int(self._ttl_s * 1_000_000_000),
            )
            return self._seq

    def get(self) -> LastFrame | None:
        """The held frame, or ``None`` when there is none or it has expired."""
        with self._lock:
            frame = self._frame
            if frame is None:
                return None
            if self._clock() >= frame.expires_at_ns:
                self._frame = None
                return None
            return frame

    def clear(self) -> None:
        with self._lock:
            self._frame = None


_mirror = LastFrameMirror()


def get_last_frame_mirror() -> LastFrameMirror:
    """The process-wide mirror the deck route reads from."""
    return _mirror


__all__ = ["LastFrame", "LastFrameMirror", "get_last_frame_mirror"]
