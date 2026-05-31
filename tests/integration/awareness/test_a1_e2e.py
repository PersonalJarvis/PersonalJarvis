"""Integration-Tests fuer Phase A1 — E2E mit echtem Win32.

Skippt auf Linux/Mac (Plan §5 AC). Spawnt notepad.exe als bekannten
Foreground-Trigger und misst SetWinEventHook-Latenz + UnhookWinEvent-
Cleanup + 2s-Shutdown-Budget.

Wird NICHT von der Standard-Test-Suite eingesammelt wenn auf non-Windows
laeuft — pytestmark skipt das ganze Modul. Im Worktree-Pfad
``<your-home>\\Desktop\\jarvis-a0`` (Win32) laufen die Tests
real; in Linux-CI: skipped.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Win32-only — Hook-Lifecycle-Test braucht echten Pump-Thread",
)

# Imports duerfen auf Linux NICHT crashen — Lazy-Imports in den Watchers
# stellen das sicher (siehe HN3). Wenn doch: Module-Top-Import von win32event
# o.ae. wurde versehentlich eingefuehrt → Sofort fixen.
from jarvis.awareness.config import AwarenessConfig  # noqa: E402
from jarvis.awareness.manager import AwarenessManager  # noqa: E402
from jarvis.awareness.privacy import PrivacyFilter  # noqa: E402
from jarvis.awareness.watchers.idle import IdleDetector  # noqa: E402
from jarvis.awareness.watchers.window import WindowFocusWatcher  # noqa: E402
from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.events import FrameUpdated, IdleEntered  # noqa: E402


def _async_collect(target: list):
    """Test-Helper: gibt einen async Handler zurueck der Events an die Liste anhaengt."""
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
        # Polling auf event (max 3s — Hook-Latenz sollte <100ms sein)
        for _ in range(60):
            if any(ev.process_name.lower() == "notepad.exe" for ev in received):
                break
            await asyncio.sleep(0.05)

        assert any(ev.process_name.lower() == "notepad.exe" for ev in received), (
            f"Kein FrameUpdated fuer notepad.exe. "
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


@pytest.mark.asyncio
async def test_idle_detector_real_threshold_2s() -> None:
    """IdleDetector mit threshold_s=2 — wartet 3.5s ohne Mouse/KB-Sim.

    Annahme: Test laeuft in nicht-interaktiver Session (CI/headless).
    Bei interaktiver Session mit Maus-Bewegung kann der Test fehlschlagen
    — dann gilt's als environmental noise, nicht als A1-Regression.
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
            "Erwartet: IdleEntered nach 3.5s Inaktivitaet. "
            "Falls fehlgeschlagen: vermutlich interaktive Session mit Mouse-Movement."
        )
    finally:
        await detector.stop()
