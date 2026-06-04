"""Tests fuer jarvis.awareness.manager.AwarenessManager (extended in A1)."""
from __future__ import annotations

import time

import pytest

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.core.bus import EventBus


@pytest.mark.asyncio
async def test_a0_backward_compat_no_bus_no_crash() -> None:
    """A0-Stub-Use: Manager ohne Bus laeuft fuer pure-Read-Use-Cases."""
    cfg = AwarenessConfig.default()
    manager = AwarenessManager(cfg)
    assert manager.state is not None
    assert manager.config is cfg


@pytest.mark.asyncio
async def test_start_with_disabled_config_skips_watchers() -> None:
    """enabled=False → start() ist no-op."""
    cfg = AwarenessConfig(enabled=False)
    bus = EventBus()
    manager = AwarenessManager(cfg, bus=bus)

    await manager.start()
    await manager.stop()


@pytest.mark.asyncio
async def test_start_idempotent() -> None:
    """Doppelter start() = no-op."""
    cfg = AwarenessConfig(enabled=False)
    manager = AwarenessManager(cfg, bus=EventBus())
    await manager.start()
    await manager.start()
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_idempotent() -> None:
    """Doppelter stop() = no-op."""
    cfg = AwarenessConfig(enabled=False)
    manager = AwarenessManager(cfg, bus=EventBus())
    await manager.start()
    await manager.stop()
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_completes_within_2s() -> None:
    """Plan §5 + §10 Hard-Negative: stop() <2s."""
    cfg = AwarenessConfig(enabled=False)
    manager = AwarenessManager(cfg, bus=EventBus())
    await manager.start()

    t0 = time.perf_counter()
    await manager.stop()
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_state_property_returns_same_instance() -> None:
    """manager.state ist stabile Instanz (kein neues Objekt pro Read)."""
    manager = AwarenessManager(AwarenessConfig.default())
    s1 = manager.state
    s2 = manager.state
    assert s1 is s2
