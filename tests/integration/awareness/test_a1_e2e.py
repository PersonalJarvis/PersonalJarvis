"""Integration tests for Phase A1 — E2E with real Win32.

Skips on Linux/Mac (Plan §5 AC). Spawns notepad.exe as a known
foreground trigger and measures SetWinEventHook latency + UnhookWinEvent
cleanup + the 2s shutdown budget.

NOT collected by the standard test suite when running on non-Windows —
pytestmark skips the whole module. On the worktree path
``<USER_HOME>\\Desktop\\jarvis-a0`` (Win32) the tests run
for real; on Linux CI: skipped.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Win32-only — hook lifecycle test needs a real pump thread",
)

# Imports must NOT crash on Linux — lazy imports in the watchers make sure of
# that (see HN3). If they do: a module-top import of win32event or similar
# was accidentally introduced → fix immediately.
from jarvis.awareness.config import AwarenessConfig  # noqa: E402
from jarvis.awareness.manager import AwarenessManager  # noqa: E402
from jarvis.awareness.privacy import PrivacyFilter  # noqa: E402
from jarvis.awareness.watchers.idle import IdleDetector  # noqa: E402
from jarvis.awareness.watchers.window import WindowFocusWatcher  # noqa: E402
from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.events import FrameUpdated, IdleEntered  # noqa: E402


def _async_collect(target: list):
    """Test helper: returns an async handler that appends events to the list."""
    async def _handler(ev):
        target.append(ev)
    return _handler


@pytest.mark.asyncio
async def test_window_focus_watcher_real_win32_hook() -> None:
    """Real SetWinEventHook: spawn notepad → FrameUpdated empfangen."""
    cfg = AwarenessConfig.default()
    bus = EventBus()
    manager = AwarenessManager(cfg)
    privacy = PrivacyFilter(cfg)

    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.start()

    proc = None
    try:
        proc = subprocess.Popen(["notepad.exe"])
        # Poll for the event (max 3s — hook latency should be <100ms)
        for _ in range(60):
            if any(ev.process_name.lower() == "notepad.exe" for ev in received):
                break
            await asyncio.sleep(0.05)

        assert any(ev.process_name.lower() == "notepad.exe" for ev in received), (
            f"No FrameUpdated for notepad.exe. "
            f"Received: {[(ev.process_name, ev.window_title) for ev in received]}"
        )
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        await watcher.stop()


@pytest.mark.asyncio
async def test_window_focus_watcher_double_lifecycle() -> None:
    """start → stop → start → stop ohne Crash (UnhookWinEvent funktioniert)."""
    cfg = AwarenessConfig.default()
    bus = EventBus()
    manager = AwarenessManager(cfg)
    privacy = PrivacyFilter(cfg)

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)

    await watcher.start()
    await watcher.stop()
    await watcher.start()
    await watcher.stop()


@pytest.mark.asyncio
async def test_window_focus_watcher_stop_under_2s() -> None:
    """Plan §5 AC: stop() returnt innerhalb 2s."""
    cfg = AwarenessConfig.default()
    bus = EventBus()
    manager = AwarenessManager(cfg)
    privacy = PrivacyFilter(cfg)

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.start()

    t0 = time.perf_counter()
    await watcher.stop()
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"stop() took {elapsed:.2f}s, exceeds 2s budget"


@pytest.mark.skip_ci
@pytest.mark.asyncio
async def test_idle_detector_real_threshold_2s() -> None:
    """IdleDetector with threshold_s=2 — waits 3.5s with no mouse/KB sim.

    Assumption: test runs in a non-interactive session (CI/headless).
    In an interactive session with mouse movement the test can fail
    — that counts as environmental noise, not an A1 regression.
    """
    cfg = AwarenessConfig.default()
    bus = EventBus()
    manager = AwarenessManager(cfg)

    received: list[IdleEntered] = []
    bus.subscribe(IdleEntered, _async_collect(received))

    detector = IdleDetector(manager=manager, bus=bus, threshold_s=2)
    await detector.start()
    try:
        await asyncio.sleep(3.5)
        assert manager.state.is_idle is True or len(received) > 0, (
            "Expected: IdleEntered after 3.5s of inactivity. "
            "If this fails: likely an interactive session with mouse movement."
        )
    finally:
        await detector.stop()
