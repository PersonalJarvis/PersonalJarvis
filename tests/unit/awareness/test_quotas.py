"""Tests fuer jarvis.awareness.quotas.StorageQuota.

A0-Scope: reine Datenklasse mit would_exceed-Verdict. A2 verwendet das
beim Episode-Persistieren als Pre-Insert-Check.
"""
from __future__ import annotations

from jarvis.awareness.quotas import StorageQuota


def test_default_thresholds() -> None:
    """Defaults: 50 MiB Bytes, 1000 Episoden."""
    q = StorageQuota()
    assert q.max_bytes == 50 * 1024 * 1024
    assert q.max_episodes == 1000


def test_would_exceed_below_caps() -> None:
    """Beide Werte unter Cap → (False, '')."""
    q = StorageQuota()
    exceeded, reason = q.would_exceed(current_bytes=1024, current_episode_count=10)
    assert exceeded is False
    assert reason == ""


def test_would_exceed_at_byte_cap() -> None:
    """current_bytes erreicht max_bytes → blockt."""
    q = StorageQuota(max_bytes=1000, max_episodes=100)
    exceeded, reason = q.would_exceed(current_bytes=1000, current_episode_count=0)
    assert exceeded is True
    assert reason == "max_bytes_reached"


def test_would_exceed_at_episode_cap() -> None:
    """current_episode_count erreicht max_episodes → blockt."""
    q = StorageQuota(max_bytes=10**9, max_episodes=5)
    exceeded, reason = q.would_exceed(current_bytes=0, current_episode_count=5)
    assert exceeded is True
    assert reason == "max_episodes_reached"


def test_would_exceed_byte_takes_priority_when_both_full() -> None:
    """Wenn beide Caps voll: Bytes-Reason gewinnt (Disk-Risk > Count-Risk)."""
    q = StorageQuota(max_bytes=100, max_episodes=10)
    exceeded, reason = q.would_exceed(current_bytes=100, current_episode_count=10)
    assert exceeded is True
    assert reason == "max_bytes_reached"


def test_would_exceed_above_caps_still_blocks() -> None:
    """Auch Werte ueber Cap (Race-Condition) → blockt."""
    q = StorageQuota(max_bytes=100, max_episodes=10)
    exceeded, reason = q.would_exceed(current_bytes=200, current_episode_count=20)
    assert exceeded is True
    assert reason == "max_bytes_reached"


def test_storagequota_is_frozen() -> None:
    """Quotas sind Konfig-Snapshots — frozen verhindert Mutation."""
    import dataclasses

    import pytest

    q = StorageQuota()
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.max_bytes = 0  # type: ignore[misc]
