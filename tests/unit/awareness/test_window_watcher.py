"""Tests for jarvis.awareness.watchers.window.WindowFocusWatcher.

Strategy: fake pump (no real Win32 hook) plus an injected
``_resolve_window_meta`` plus a direct call to ``_drain_once()``. This lets
tests run on any platform; the real hook lifecycle is in the integration
test ``test_a1_e2e.py``.

Architecture assumptions the Wave 3 implementation dictates:
- ``_drain_once()`` is isolatable (one iteration of the drain loop).
- ``_safe_enqueue((ts_ns, hwnd))`` pushes items onto the queue (asyncio-thread-safe).
- ``_resolve_window_meta(hwnd)`` is a staticmethod — patchable.
- ``_loop`` is a property set in tests before _safe_enqueue.
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
    """Test helper: returns an async handler that appends events to the list."""
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
    """Frame with a banking title → AwarenessCaptureBlocked, NO FrameUpdated."""
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
    """Duplicate hwnd <50ms apart gets dropped — Win32 sometimes emits 2-3."""
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
    """Different hwnds within <50ms are NOT deduplicated."""
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
    """When the queue is full: drop counter +1, no crash, no exception."""
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    watcher._loop = asyncio.get_running_loop()

    # Queue ueberlaufen lassen (maxsize=64)
    for i in range(80):
        watcher._safe_enqueue((time.time_ns() + i, i))

    assert watcher._drops > 0


@pytest.mark.asyncio
async def test_publish_updates_state_current_frame() -> None:
    """After drain: manager.state.current_frame is set."""
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
    """On Linux: start() is a no-op, no Win32 crash, a duplicate call is ok."""
    if os.name == "nt":
        pytest.skip("Linux-only — Win32-start startet echten Pump-Thread")
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.start()
    await watcher.start()  # no crash
    await watcher.stop()


@pytest.mark.asyncio
async def test_stop_idempotent_no_start() -> None:
    """stop() ohne start() ist no-op."""
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.stop()  # no crash, no hang
    await watcher.stop()


@pytest.mark.asyncio
async def test_resolve_meta_failure_skips_frame_no_crash() -> None:
    """If _resolve_window_meta raises: the frame is dropped, no crash."""
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
