"""Tests fuer jarvis.awareness.watchers.window.WindowFocusWatcher.

Strategie: Fake-Pump (kein echter Win32-Hook) plus injiziertes
``_resolve_window_meta`` plus direkter Aufruf von ``_drain_once()``. Damit
laufen Tests auf jeder Plattform; echte Hook-Lifecycle ist im Integration-
Test ``test_a1_e2e.py``.

Architektur-Annahmen die der Implementation in Welle 3 vorgeben:
- ``_drain_once()`` ist isolierbar (eine Iteration der Drain-Loop).
- ``_safe_enqueue((ts_ns, hwnd))`` schiebt Items in die Queue (asyncio-thread-safe).
- ``_resolve_window_meta(hwnd)`` ist staticmethod — patchbar.
- ``_loop`` ist Property die in Tests vor _safe_enqueue gesetzt wird.
"""
from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import patch

import pytest

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.privacy import PrivacyFilter
from jarvis.awareness.watchers.window import WindowFocusWatcher
from jarvis.core.bus import EventBus
from jarvis.core.events import AwarenessCaptureBlocked, FrameUpdated


def _make_components() -> tuple[EventBus, AwarenessManager, PrivacyFilter]:
    cfg = AwarenessConfig.default()
    bus = EventBus()
    manager = AwarenessManager(cfg)
    privacy = PrivacyFilter(cfg)
    return bus, manager, privacy


def _async_collect(target: list):
    """Test-Helper: gibt einen async Handler zurueck der Events an die Liste anhaengt."""
    async def _handler(ev):
        target.append(ev)
    return _handler


@pytest.mark.asyncio
async def test_drain_routes_allowed_to_frame_updated() -> None:
    """Frame mit allowed Privacy → FrameUpdated published."""
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_resolve_window_meta",
        staticmethod(lambda hwnd: ("pipeline.py - Visual Studio Code", 1234, "code.exe")),
    ):
        watcher._loop = asyncio.get_running_loop()
        watcher._safe_enqueue((time.time_ns(), 999))
        await watcher._drain_once()

    assert len(received) == 1
    assert received[0].process_name == "code.exe"
    assert received[0].is_capture_allowed is True


@pytest.mark.asyncio
async def test_drain_routes_blocked_to_capture_blocked() -> None:
    """Frame mit Banking-Title → AwarenessCaptureBlocked, KEIN FrameUpdated."""
    bus, manager, privacy = _make_components()
    blocked: list[AwarenessCaptureBlocked] = []
    updated: list[FrameUpdated] = []
    bus.subscribe(AwarenessCaptureBlocked, _async_collect(blocked))
    bus.subscribe(FrameUpdated, _async_collect(updated))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_resolve_window_meta",
        staticmethod(lambda hwnd: ("Sparkasse Online-Banking", 5678, "firefox.exe")),
    ):
        watcher._loop = asyncio.get_running_loop()
        watcher._safe_enqueue((time.time_ns(), 999))
        await watcher._drain_once()

    assert len(blocked) == 1
    assert len(updated) == 0
    assert "matched_blocked_title" in blocked[0].reason


@pytest.mark.asyncio
async def test_dedupe_50ms_window() -> None:
    """Doppelter hwnd <50ms apart wird gedropt — Win32 emittiert manchmal 2-3."""
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_resolve_window_meta",
        staticmethod(lambda hwnd: ("My Doc - Notepad", 1, "notepad.exe")),
    ):
        watcher._loop = asyncio.get_running_loop()
        ts = time.time_ns()
        watcher._safe_enqueue((ts, 100))
        await watcher._drain_once()
        watcher._safe_enqueue((ts + 10_000_000, 100))  # 10ms spaeter
        await watcher._drain_once()

    assert len(received) == 1


@pytest.mark.asyncio
async def test_dedupe_does_not_drop_different_hwnd() -> None:
    """Verschiedene hwnds in <50ms werden NICHT dedupliziert."""
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_resolve_window_meta",
        staticmethod(lambda hwnd: (f"hwnd-{hwnd}", hwnd, "notepad.exe")),
    ):
        watcher._loop = asyncio.get_running_loop()
        ts = time.time_ns()
        watcher._safe_enqueue((ts, 100))
        await watcher._drain_once()
        watcher._safe_enqueue((ts + 10_000_000, 200))  # anderer hwnd
        await watcher._drain_once()

    assert len(received) == 2


@pytest.mark.asyncio
async def test_queue_full_increments_drops_no_crash() -> None:
    """Bei voller Queue: drop-counter +1, kein Crash, kein Exception."""
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    watcher._loop = asyncio.get_running_loop()

    # Queue ueberlaufen lassen (maxsize=64)
    for i in range(80):
        watcher._safe_enqueue((time.time_ns() + i, i))

    assert watcher._drops > 0


@pytest.mark.asyncio
async def test_publish_updates_state_current_frame() -> None:
    """Nach Drain: manager.state.current_frame ist gesetzt."""
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_resolve_window_meta",
        staticmethod(lambda hwnd: ("Test - Notepad", 999, "notepad.exe")),
    ):
        watcher._loop = asyncio.get_running_loop()
        watcher._safe_enqueue((time.time_ns(), 100))
        await watcher._drain_once()

    assert manager.state.current_frame is not None
    assert manager.state.current_frame.active_window_title == "Test - Notepad"
    assert manager.state.current_frame.active_process_name == "notepad.exe"


@pytest.mark.asyncio
async def test_start_idempotent_on_linux() -> None:
    """Auf Linux: start() ist no-op, kein Win32-Crash, doppelter call ok."""
    if os.name == "nt":
        pytest.skip("Linux-only — Win32-start startet echten Pump-Thread")
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.start()
    await watcher.start()  # kein Crash
    await watcher.stop()


@pytest.mark.asyncio
async def test_stop_idempotent_no_start() -> None:
    """stop() ohne start() ist no-op."""
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.stop()  # kein Crash, kein Hang
    await watcher.stop()


@pytest.mark.asyncio
async def test_resolve_meta_failure_skips_frame_no_crash() -> None:
    """Wenn _resolve_window_meta raised: Frame wird gedropt, kein Crash."""
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    def _raises(hwnd: int) -> tuple[str, int, str]:
        raise RuntimeError("simulated failure")

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_resolve_window_meta", staticmethod(_raises),
    ):
        watcher._loop = asyncio.get_running_loop()
        watcher._safe_enqueue((time.time_ns(), 100))
        await watcher._drain_once()

    assert len(received) == 0
