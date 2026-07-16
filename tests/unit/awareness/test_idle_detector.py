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

import sys
import time
from unittest.mock import patch

import pytest

import jarvis.awareness.watchers.idle as idle_mod
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


# ---- Backend resolution (macOS/Linux) ----------------------------------------
# Platform behavior is simulated by monkeypatching the resolvers idle.py
# imports (detect_platform / display_present / is_wayland) so these tests run
# deterministically on any host, including this Windows dev machine.


def test_resolve_backend_windows() -> None:
    with patch.object(idle_mod, "detect_platform", lambda: "win32"):
        backend, reason = IdleDetector._resolve_backend()
    assert backend == "win32"
    assert reason == ""


def test_resolve_backend_macos_without_quartz(monkeypatch) -> None:
    """No pyobjc-Quartz importable → backend=None with a clear reason.

    Forced via ``sys.modules["Quartz"] = None`` (the standard "known
    unimportable" sentinel) so this is deterministic regardless of whether
    the host actually has pyobjc installed.
    """
    monkeypatch.setattr(idle_mod, "detect_platform", lambda: "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", None)
    backend, reason = IdleDetector._resolve_backend()
    assert backend is None
    assert "Quartz" in reason


def test_resolve_backend_linux_wayland(monkeypatch) -> None:
    monkeypatch.setattr(idle_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(idle_mod, "is_wayland", lambda: True)
    backend, reason = IdleDetector._resolve_backend()
    assert backend is None
    assert "Wayland" in reason


def test_resolve_backend_linux_headless(monkeypatch) -> None:
    monkeypatch.setattr(idle_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(idle_mod, "is_wayland", lambda: False)
    monkeypatch.setattr(idle_mod, "display_present", lambda: False)
    backend, reason = IdleDetector._resolve_backend()
    assert backend is None
    assert "headless" in reason


def test_resolve_backend_linux_missing_xprintidle(monkeypatch) -> None:
    monkeypatch.setattr(idle_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(idle_mod, "is_wayland", lambda: False)
    monkeypatch.setattr(idle_mod, "display_present", lambda: True)
    monkeypatch.setattr(idle_mod.shutil, "which", lambda name: None)
    backend, reason = IdleDetector._resolve_backend()
    assert backend is None
    assert "xprintidle" in reason


def test_resolve_backend_linux_available(monkeypatch) -> None:
    monkeypatch.setattr(idle_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(idle_mod, "is_wayland", lambda: False)
    monkeypatch.setattr(idle_mod, "display_present", lambda: True)
    monkeypatch.setattr(idle_mod.shutil, "which", lambda name: "/usr/bin/xprintidle")
    backend, reason = IdleDetector._resolve_backend()
    assert backend == "linux"
    assert reason == ""


@pytest.mark.asyncio
async def test_start_degrades_honestly_when_no_backend(monkeypatch, caplog) -> None:
    """No usable backend: start() logs one line and never creates the task
    (mirrors WindowFocusWatcher's honest degradation), instead of spinning
    the 1 s loop forever reporting "never idle"."""
    monkeypatch.setattr(idle_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(idle_mod, "is_wayland", lambda: False)
    monkeypatch.setattr(idle_mod, "display_present", lambda: False)

    detector = IdleDetector(manager=_make_manager(), bus=EventBus(), threshold_s=300)
    with caplog.at_level("INFO"):
        await detector.start()

    assert detector._task is None
    assert any("idle detection unavailable" in r.message for r in caplog.records)
    await detector.stop()    # no crash on stop-without-start


@pytest.mark.asyncio
async def test_tick_disables_after_max_consecutive_failures() -> None:
    """A backend that always raises stops the loop after N failures, not
    before, and never crashes the tick."""
    detector = IdleDetector(manager=_make_manager(), bus=EventBus(), threshold_s=300)
    detector._backend = "linux"

    def _raise() -> float:
        raise RuntimeError("xprintidle vanished")

    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(_raise)):
        for _ in range(idle_mod._MAX_CONSECUTIVE_FAILURES - 1):
            await detector._tick_once()
            assert detector._stopped is False
        await detector._tick_once()

    assert detector._stopped is True
    assert detector._consecutive_failures == idle_mod._MAX_CONSECUTIVE_FAILURES


@pytest.mark.asyncio
async def test_tick_resets_failure_counter_on_success() -> None:
    """A single successful tick after failures resets the counter to zero."""
    detector = IdleDetector(manager=_make_manager(), bus=EventBus(), threshold_s=300)
    detector._backend = "linux"
    detector._consecutive_failures = idle_mod._MAX_CONSECUTIVE_FAILURES - 1

    with patch.object(IdleDetector, "_get_idle_seconds", staticmethod(lambda: 0.0)):
        await detector._tick_once()

    assert detector._consecutive_failures == 0
    assert detector._stopped is False


def test_get_idle_seconds_macos_backend(monkeypatch) -> None:
    """The macOS backend calls CGEventSourceSecondsSinceLastEventType and
    converts the result to a float."""
    import types

    fake_quartz = types.SimpleNamespace(
        CGEventSourceSecondsSinceLastEventType=lambda state, event_type: 12.5,
        kCGEventSourceStateCombinedSessionState=object(),
        kCGAnyInputEventType=object(),
    )
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)

    detector = IdleDetector(manager=_make_manager(), bus=EventBus(), threshold_s=300)
    detector._backend = "macos"
    assert detector._get_idle_seconds() == 12.5


def test_get_idle_seconds_linux_backend(monkeypatch) -> None:
    """The Linux backend parses xprintidle's stdout (milliseconds) to seconds."""
    class _FakeCompleted:
        returncode = 0
        stdout = "4200\n"

    monkeypatch.setattr(
        idle_mod.subprocess, "run", lambda *a, **kw: _FakeCompleted(),
    )

    detector = IdleDetector(manager=_make_manager(), bus=EventBus(), threshold_s=300)
    detector._backend = "linux"
    assert detector._get_idle_seconds() == 4.2


def test_get_idle_seconds_linux_backend_raises_on_nonzero_exit(monkeypatch) -> None:
    """A non-zero xprintidle exit raises — the tick-level failure counter
    is what disables the backend, not a silent 0.0 here."""
    class _FakeCompleted:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(
        idle_mod.subprocess, "run", lambda *a, **kw: _FakeCompleted(),
    )

    detector = IdleDetector(manager=_make_manager(), bus=EventBus(), threshold_s=300)
    detector._backend = "linux"
    with pytest.raises(RuntimeError):
        detector._get_idle_seconds()
