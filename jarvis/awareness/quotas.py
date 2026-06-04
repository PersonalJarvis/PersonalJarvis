"""Storage quotas for awareness persistence (Phase A2+).

In A0 only the data class exists. In A2 the ``StoryTracker`` calls
``would_exceed()`` before every episode insert — on a cap violation the
retention policy kicks in (prune the oldest episode or block the insert).
Bytes are weighted ahead of count because disk pressure (full disk) has
higher consequences than index bloat.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StorageQuota:
    """Bytes and episode cap as a config snapshot.

    Frozen because it is built from config at init time and is not
    mutated at runtime (hot-reload creates a new instance).
    """
    max_bytes: int = 50 * 1024 * 1024     # 50 MiB Default
    max_episodes: int = 1000

    def would_exceed(
        self,
        *,
        current_bytes: int,
        current_episode_count: int,
    ) -> tuple[bool, str]:
        """Returns (exceeded, reason).

        Reason is empty when below cap; otherwise ``"max_bytes_reached"``
        or ``"max_episodes_reached"``. The bytes cap takes priority —
        disk pressure > index pressure.
        """
        if current_bytes >= self.max_bytes:
            return True, "max_bytes_reached"
        if current_episode_count >= self.max_episodes:
            return True, "max_episodes_reached"
        return False, ""
