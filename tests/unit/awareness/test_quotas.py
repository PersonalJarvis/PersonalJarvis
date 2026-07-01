"""Tests for jarvis.awareness.quotas.StorageQuota.

A0 scope: a pure dataclass with a would_exceed verdict. A2 uses it
during episode persistence as a pre-insert check.
"""
from __future__ import annotations

from jarvis.awareness.quotas import StorageQuota


def test_default_thresholds() -> None:
    """Defaults: 50 MiB bytes, 1000 episodes."""
    q = StorageQuota()
    assert q.max_bytes == 50 * 1024 * 1024
    assert q.max_episodes == 1000


def test_would_exceed_below_caps() -> None:
    """Both values under the cap → (False, '')."""
    q = StorageQuota()
    exceeded, reason = q.would_exceed(current_bytes=1024, current_episode_count=10)
    assert exceeded is False
    assert reason == ""


def test_would_exceed_at_byte_cap() -> None:
    """current_bytes reaches max_bytes → blocks."""
    q = StorageQuota(max_bytes=1000, max_episodes=100)
    exceeded, reason = q.would_exceed(current_bytes=1000, current_episode_count=0)
    assert exceeded is True
    assert reason == "max_bytes_reached"


def test_would_exceed_at_episode_cap() -> None:
    """current_episode_count reaches max_episodes → blocks."""
    q = StorageQuota(max_bytes=10**9, max_episodes=5)
    exceeded, reason = q.would_exceed(current_bytes=0, current_episode_count=5)
    assert exceeded is True
    assert reason == "max_episodes_reached"


def test_would_exceed_byte_takes_priority_when_both_full() -> None:
    """When both caps are full: the bytes reason wins (disk risk > count risk)."""
    q = StorageQuota(max_bytes=100, max_episodes=10)
    exceeded, reason = q.would_exceed(current_bytes=100, current_episode_count=10)
    assert exceeded is True
    assert reason == "max_bytes_reached"


def test_would_exceed_above_caps_still_blocks() -> None:
    """Values above the cap too (race condition) → blocks."""
    q = StorageQuota(max_bytes=100, max_episodes=10)
    exceeded, reason = q.would_exceed(current_bytes=200, current_episode_count=20)
    assert exceeded is True
    assert reason == "max_bytes_reached"


def test_storagequota_is_frozen() -> None:
    """Quotas are config snapshots — frozen prevents mutation."""
    import dataclasses

    import pytest

    q = StorageQuota()
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.max_bytes = 0  # type: ignore[misc]
