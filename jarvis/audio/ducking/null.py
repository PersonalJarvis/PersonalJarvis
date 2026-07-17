"""No-op ducker (no platform ducking backend available on this host)."""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.audio.ducking")


class NullDucker:
    """Audio ducking unavailable on this host — every call is a no-op."""

    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        return []

    def restore(self, pids: list[int]) -> None:
        return None
