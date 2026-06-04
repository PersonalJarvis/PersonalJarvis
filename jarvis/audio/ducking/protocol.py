"""The audio-ducker backend contract."""
from __future__ import annotations

from typing import Protocol


class AudioDucker(Protocol):
    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        """Mute every other app's audio session. Returns the PIDs muted."""
        ...

    def restore(self, pids: list[int]) -> None:
        """Unmute exactly the given PIDs."""
        ...
