"""Tests fuer jarvis.awareness.watchers.idle.IdleDetector.

Strategie: Mock von ``_get_idle_seconds`` plus direkter Aufruf von
``_tick_once()``. Damit laufen Tests <100ms ohne echten 5min-Wait und
ohne Win32 — pytest funktioniert auf jeder Plattform.

Architektur-Annahmen die der Implementation in Welle 2 vorgeben:
- ``_tick_once()`` ist eine isolierbare Method (1 Tick = 1 GetLastInputInfo
  + Transition-Check + Event-Publish). ``_run()`` ruft sie in Schleife
  mit ``asyncio.sleep(1)`` zwischen Ticks.
- ``_get_idle_seconds()`` ist staticmethod — patchbar via
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
    """Test-Helper: gibt einen async Handler zurueck der Events an die Liste anhaengt."""
    async def _handler(ev):
        target.append(ev)
    return _handler


@pytest.mark.asyncio
async def test_active_to_idle_transition_emits_event() -> None:
    """Wenn _get_idle_seconds >= threshold: IdleEntered + state.is_idle=True."""
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
    """Sobald _get_idle_seconds < threshold und vorher idle: IdleExited."""
    bus = EventBus()
    exited: list[IdleExited] = []
    bus.subscribe(IdleExited, _async_collect(exited))

    manager = _make_manager()
    detector = IdleDetector(manager=manager, bus=bus, threshold_s=5)

    # Erst idle machen
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 6.0)):
        await detector._tick_once()
    assert manager.state.is_idle is True

    # Dann wieder aktiv
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 1.0)):
        await detector._tick_once()

    assert manager.state.is_idle is False
    assert len(exited) == 1
    assert exited[0].was_idle_for_ms >= 0


@pytest.mark.asyncio
async def test_idle_since_ns_propagates_into_current_frame() -> None:
    """Wenn current_frame existiert: idle_since_ns wird via dataclasses.replace gesetzt."""
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
    # Original-Felder bleiben — replace, nicht overwrite
    assert cur.active_window_title == "VS Code"
    assert cur.active_process_name == "code.exe"


@pytest.mark.asyncio
async def test_no_event_when_below_threshold() -> None:
    """Mehrere Ticks unter threshold → keine Events."""
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
    """Bleibt idle ueber mehrere Ticks → genau 1 IdleEntered."""
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
    """Doppelter start() ist no-op."""
    bus = EventBus()
    detector = IdleDetector(manager=_make_manager(), bus=bus, threshold_s=300)
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 0.0)):
        await detector.start()
        await detector.start()
        await detector.stop()


@pytest.mark.asyncio
async def test_stop_idempotent_and_fast() -> None:
    """stop() bricht in <1s ab. Doppelter stop() ist no-op."""
    bus = EventBus()
    detector = IdleDetector(manager=_make_manager(), bus=bus, threshold_s=300)
    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 0.0)):
        await detector.start()
        t0 = time.perf_counter()
        await detector.stop()
        await detector.stop()
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.5
