"""In-memory home of appshots — nothing here ever touches the disk.

Two slots, with two different promises:

* **pending** — the appshot waiting for the next message. Single use: the
  first turn (or the chat composer) that takes it removes it, and it expires
  after ``[screen_context].ttl_s`` like any other unconsumed capture.
* **latest** — the one picture the Appshots page may show, so the user can see
  what the assistant was shown. Replaced by the next appshot and dropped after
  ``[screen_context].deck_preview_s`` (``0`` = never kept).

Both hold bytes, never paths (the Screen Context retention contract).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Appshot:
    """One captured, already-redacted window or monitor."""

    id: str
    image: bytes
    mime: str
    width: int
    height: int
    #: Metadata-safe label ("active window" / "monitor X") for events and logs.
    label: str
    #: Front application name — shown in the app only, never published.
    app_name: str
    #: Model-facing evidence block that rides with the image.
    note: str
    #: Scrubbed on-screen text, when the accessibility layer had any.
    ui_text: str
    #: ``hotkey`` | ``voice`` | ``tool`` | ``button``.
    trigger: str
    #: Wall-clock seconds.
    taken_at: float
    delivered_to: str = ""

    def meta(self) -> dict[str, object]:
        """What the app may show about it — no pixels."""
        return {
            "id": self.id,
            "width": self.width,
            "height": self.height,
            "label": self.label,
            "app_name": self.app_name,
            "trigger": self.trigger,
            "taken_at": self.taken_at,
            "delivered_to": self.delivered_to,
        }


class AppshotStore:
    """Thread-safe pending + latest slots with monotonic expiry."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: Appshot | None = None
        self._pending_until = 0.0
        self._latest: Appshot | None = None
        self._latest_until = 0.0

    def remember(self, shot: Appshot, *, keep_s: float) -> None:
        """Make ``shot`` the picture the Appshots page shows."""
        with self._lock:
            if keep_s <= 0:
                self._latest = None
                return
            self._latest = shot
            self._latest_until = self._clock() + keep_s

    def park(self, shot: Appshot, *, ttl_s: float) -> None:
        """Hold ``shot`` for the next message, replacing an older one."""
        with self._lock:
            self._pending = shot
            self._pending_until = self._clock() + max(1.0, ttl_s)

    def take_pending(self, shot_id: str | None = None) -> Appshot | None:
        """Remove and return the pending appshot (optionally only a given id)."""
        with self._lock:
            shot = self._live_pending()
            if shot is None or (shot_id and shot.id != shot_id):
                return None
            self._pending = None
            return shot

    def peek_pending(self) -> Appshot | None:
        with self._lock:
            return self._live_pending()

    def latest(self) -> Appshot | None:
        with self._lock:
            if self._latest is not None and self._clock() >= self._latest_until:
                self._latest = None
            return self._latest

    def mark_delivered(self, shot_id: str, delivered_to: str) -> None:
        with self._lock:
            if self._latest is not None and self._latest.id == shot_id:
                self._latest = replace(self._latest, delivered_to=delivered_to)

    def clear(self) -> None:
        with self._lock:
            self._pending = None
            self._latest = None

    def _live_pending(self) -> Appshot | None:
        if self._pending is not None and self._clock() >= self._pending_until:
            self._pending = None
        return self._pending


_STORE = AppshotStore()


def get_store() -> AppshotStore:
    return _STORE


__all__ = ["Appshot", "AppshotStore", "get_store"]
