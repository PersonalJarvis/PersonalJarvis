"""Tests for jarvis.awareness.watchers.idle.IdleDetector.

Strategy: mock ``_get_idle_seconds`` plus a direct call to
``_tick_once()``. This lets tests run <100ms without a real 5min wait and
without Win32 — pytest works on any platform.

Architecture assumptions that the wave-2 implementation is bound to:
- ``_tick_once()`` is an isolable method (1 tick = 1 GetLastInputInfo
  + transition check + event publish). ``_run()`` calls it in a loop
  with ``asyncio.sleep(1)`` between ticks.
- ``_get_idle_seconds()`` is a staticmethod — patchable via
  ``patch.object(IdleDetector, "_get_idle_seconds", ...)``.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.state import FrameSnapshot
from jarvis.awareness.watchers.idle import IdleDetector
from jarvis.core.bus import EventBus
from jarvis.core.events import IdleEntered, IdleExited


def _make_manager() -> AwarenessManager:
    return AwarenessManager(AwarenessConfig.default())


def _async_collect(target: list):
    """Test helper: returns an async handler that appends events to the list."""
    async def _handler(ev):
        target.append(ev)
    return _handler


@pytest.mark.asyncio
async def test_active_to_idle_transition_emits_event() -> None:
    """When _get_idle_seconds >= threshold: IdleEntered + state.is_idle=True."""
    bus = EventBus()
    received: list[IdleEntered] = []
    bus.subscribe(IdleEntered, _async_collect(received))

    manager = _make_manager()
    detector = IdleDetector(manager=manager, bus=bus, threshold_s=5)

    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 6.0)):
        await detector._tick_once()

    assert manager.state.is_idle is True
    assert len(received) == 1
    assert received[0].idle_since_ns > 0


@pytest.mark.asyncio
async def test_idle_to_active_transition_emits_exited() -> None:
    """As soon as _get_idle_seconds < threshold and it was idle before: IdleExited."""
    bus = EventBus()
    exited: list[IdleExited] = []
    bus.subscribe(IdleExited, _async_collect(exited))

    manager = _make_manager()
    detector = IdleDetector(manager=manager, bus=bus, threshold_s=5)

    # First make it idle
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 6.0)):
        await detector._tick_once()
    assert manager.state.is_idle is True

    # Then active again
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 1.0)):
        await detector._tick_once()

    assert manager.state.is_idle is False
    assert len(exited) == 1
    assert exited[0].was_idle_for_ms >= 0


@pytest.mark.asyncio
async def test_idle_since_ns_propagates_into_current_frame() -> None:
    """If current_frame exists: idle_since_ns is set via dataclasses.replace."""
    bus = EventBus()
    manager = _make_manager()
    manager.state.current_frame = FrameSnapshot(
        timestamp_ns=time.time_ns(),
        active_window_title="VS Code",
        active_process_name="code.exe",
        active_pid=1234,
        is_capture_allowed=True,
    )

    detector = IdleDetector(manager=manager, bus=bus, threshold_s=5)
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 7.0)):
        await detector._tick_once()

    cur = manager.state.current_frame
    assert cur is not None
    assert cur.idle_since_ns is not None
    # Original fields remain — replace, not overwrite
    assert cur.active_window_title == "VS Code"
    assert cur.active_process_name == "code.exe"


@pytest.mark.asyncio
async def test_no_event_when_below_threshold() -> None:
    """Multiple ticks below threshold → no events."""
    bus = EventBus()
    received: list[IdleEntered] = []
    bus.subscribe(IdleEntered, _async_collect(received))

    detector = IdleDetector(manager=_make_manager(), bus=bus, threshold_s=5)

    fake_seq = iter([4.9, 4.8, 4.5, 0.0])
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: next(fake_seq))):
        for _ in range(4):
            await detector._tick_once()

    assert len(received) == 0


@pytest.mark.asyncio
async def test_no_double_idle_event_within_same_idle_phase() -> None:
    """Stays idle over multiple ticks → exactly 1 IdleEntered."""
    bus = EventBus()
    received: list[IdleEntered] = []
    bus.subscribe(IdleEntered, _async_collect(received))

    detector = IdleDetector(manager=_make_manager(), bus=bus, threshold_s=5)
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 10.0)):
        await detector._tick_once()
        await detector._tick_once()
        await detector._tick_once()

    assert len(received) == 1


@pytest.mark.asyncio
async def test_start_idempotent() -> None:
    """A double start() is a no-op."""
    bus = EventBus()
    detector = IdleDetector(manager=_make_manager(), bus=bus, threshold_s=300)
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 0.0)):
        await detector.start()
        await detector.start()
        await detector.stop()


@pytest.mark.asyncio
async def test_stop_idempotent_and_fast() -> None:
    """stop() completes in <1s. A double stop() is a no-op."""
    bus = EventBus()
    detector = IdleDetector(manager=_make_manager(), bus=bus, threshold_s=300)
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 0.0)):
        await detector.start()
        t0 = time.perf_counter()
        await detector.stop()
        await detector.stop()
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.5
