"""In-memory home of appshots — nothing here ever touches the disk.

Two slots, with two different promises:

* **pending** — the appshot waiting for the next message. Single use: the
  first turn (or the chat composer) that takes it removes it, and it expires
  after ``[screen_context].ttl_s`` like any other unconsumed capture.
* **recent** — the last few appshots (``MAX_RECENT``), newest first, each
  dropped after ``[screen_context].deck_preview_s`` (``0`` = never kept). The
  newest is the picture the Appshots page shows (:meth:`AppshotStore.latest`);
  the others are the older cards still stacked in the screen corner, so their
  Copy, Save and Edit reach their own picture (:meth:`AppshotStore.get`).

Both hold bytes, never paths (the Screen Context retention contract).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

#: How many appshots stay at hand — as many as the corner card stack shows.
MAX_RECENT = 5


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
    """Thread-safe pending + recent slots with monotonic expiry."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: Appshot | None = None
        self._pending_until = 0.0
        #: ``(shot, keep until)``, newest first, at most ``MAX_RECENT``.
        self._recent: list[tuple[Appshot, float]] = []

    def remember(self, shot: Appshot, *, keep_s: float) -> None:
        """Make ``shot`` the newest picture (the one the Appshots page shows)."""
        with self._lock:
            if keep_s <= 0:
                self._recent.clear()
                return
            kept = [(s, until) for s, until in self._recent if s.id != shot.id]
            self._recent = [(shot, self._clock() + keep_s), *kept][:MAX_RECENT]

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
            recent = self._live_recent()
            return recent[0][0] if recent else None

    def get(self, shot_id: str) -> Appshot | None:
        """A still-kept appshot by id — an older card's own picture."""
        with self._lock:
            for shot, _until in self._live_recent():
                if shot.id == shot_id:
                    return shot
            return None

    def replace_image(
        self, shot_id: str, image: bytes, mime: str, width: int, height: int
    ) -> Appshot | None:
        """Swap in the user's edited picture for appshot ``shot_id``.

        Updates that kept appshot and, when it is still waiting for the next
        message, the pending one too — so that message carries the edit.
        ``None`` when that appshot is no longer held.
        """
        with self._lock:
            recent = self._live_recent()
            index = next((i for i, (s, _) in enumerate(recent) if s.id == shot_id), None)
            if index is None:
                return None
            shot, until = recent[index]
            edited = replace(shot, image=image, mime=mime, width=width, height=height)
            recent[index] = (edited, until)
            pending = self._live_pending()
            if pending is not None and pending.id == shot_id:
                self._pending = replace(pending, image=image, mime=mime, width=width, height=height)
            return edited

    def mark_delivered(self, shot_id: str, delivered_to: str) -> None:
        with self._lock:
            for index, (shot, until) in enumerate(self._recent):
                if shot.id == shot_id:
                    self._recent[index] = (replace(shot, delivered_to=delivered_to), until)

    def clear(self) -> None:
        with self._lock:
            self._pending = None
            self._recent.clear()

    def _live_recent(self) -> list[tuple[Appshot, float]]:
        now = self._clock()
        self._recent = [(s, until) for s, until in self._recent if now < until]
        return self._recent

    def _live_pending(self) -> Appshot | None:
        if self._pending is not None and self._clock() >= self._pending_until:
            self._pending = None
        return self._pending


_STORE = AppshotStore()


def get_store() -> AppshotStore:
    return _STORE


__all__ = ["MAX_RECENT", "Appshot", "AppshotStore", "get_store"]
