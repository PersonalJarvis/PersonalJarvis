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
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import jarvis.awareness.watchers.window as window_mod
from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.privacy import PrivacyFilter
from jarvis.awareness.watchers.window import WindowFocusWatcher
from jarvis.core.bus import EventBus
from jarvis.core.events import AwarenessCaptureBlocked, FrameUpdated
from jarvis.platform.window_state import WindowInfo


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
async def test_unresolvable_window_is_not_published_as_a_frame() -> None:
    """``_resolve_window_meta`` answers ("", 0, "") for a probe that failed.

    Publishing that handed PrivacyFilter two empty strings, which match no
    block pattern and fall through to ``default_allow_for_unknown`` — so an
    unidentifiable window was waved through AND overwrote
    ``state.current_frame``, blanking the foreground context the brain reads.
    The POSIX poller already refused exactly this; Win32 never did.
    """
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    blocked: list[AwarenessCaptureBlocked] = []
    bus.subscribe(FrameUpdated, _async_collect(received))
    bus.subscribe(AwarenessCaptureBlocked, _async_collect(blocked))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_resolve_window_meta",
        staticmethod(lambda hwnd: ("", 0, "")),
    ):
        watcher._loop = asyncio.get_running_loop()
        watcher._safe_enqueue((time.time_ns(), 100))
        await watcher._drain_once()

    assert received == []
    assert blocked == []
    assert manager.state.current_frame is None


@pytest.mark.asyncio
async def test_a_title_less_window_still_publishes_when_the_process_is_known() -> None:
    """Only a TOTALLY unreadable probe is discarded: a window with no caption
    but a known owner still says which app is focused, and the process-based
    privacy rules can act on it."""
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_resolve_window_meta",
        staticmethod(lambda hwnd: ("", 4321, "notepad.exe")),
    ):
        watcher._loop = asyncio.get_running_loop()
        watcher._safe_enqueue((time.time_ns(), 100))
        await watcher._drain_once()

    assert len(received) == 1
    assert received[0].process_name == "notepad.exe"


@pytest.mark.asyncio
async def test_a_backward_clock_step_does_not_dedupe_away_later_frames() -> None:
    """The dedupe delta comes from the WALL clock, which can step backwards
    (NTP correction, a resumed VM). A negative delta is also "< 50 ms", so one
    backward step used to swallow every further event for the focused window
    and freeze focus tracking on that app until the clock caught up."""
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
        # The clock jumps a minute back; the same window is focused again.
        watcher._safe_enqueue((ts - 60_000_000_000, 100))
        await watcher._drain_once()

    assert len(received) == 2


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


# ---- POSIX polling fallback (macOS/Linux) -----------------------------------
# Platform behavior is simulated by monkeypatching the resolvers window.py
# imports (detect_platform / display_present / is_wayland) so these tests run
# deterministically on any host, including this Windows dev machine.


@pytest.mark.asyncio
async def test_posix_start_degrades_honestly_on_headless_linux(monkeypatch) -> None:
    """start() on a headless Linux host logs one line and starts no poll task."""
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(window_mod, "display_present", lambda: False)
    monkeypatch.setattr(window_mod, "is_wayland", lambda: False)

    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.start()

    assert watcher._poll_task is None
    await watcher.stop()    # idempotent no-op, no crash, no hang


@pytest.mark.asyncio
async def test_posix_start_degrades_honestly_on_wayland(monkeypatch) -> None:
    """start() on a Wayland session logs one line and starts no poll task."""
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(window_mod, "display_present", lambda: True)
    monkeypatch.setattr(window_mod, "is_wayland", lambda: True)

    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.start()

    assert watcher._poll_task is None
    await watcher.stop()


@pytest.mark.asyncio
async def test_posix_start_polls_on_macos(monkeypatch) -> None:
    """macOS always has a display — start() always starts the poll task."""
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "darwin")

    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window", staticmethod(lambda: None),
    ):
        await watcher.start()
        assert watcher._poll_task is not None
        await watcher.stop()

    assert watcher._poll_task is None


@pytest.mark.asyncio
async def test_posix_poll_once_emits_on_focus_change() -> None:
    """A changed foreground window publishes exactly one FrameUpdated."""
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window",
        staticmethod(lambda: WindowInfo(title="Terminal", handle=42)),
    ), patch.object(
        WindowFocusWatcher, "_resolve_posix_focus_meta",
        staticmethod(lambda win: (123, "Terminal.app")),
    ):
        ok_first = await watcher._poll_once()
        ok_second = await watcher._poll_once()    # unchanged focus — no re-emit

    assert ok_first is True
    assert ok_second is True
    assert len(received) == 1
    assert received[0].process_name == "Terminal.app"
    assert received[0].pid == 123
    assert manager.state.current_frame is not None
    assert manager.state.current_frame.active_window_title == "Terminal"


@pytest.mark.asyncio
async def test_posix_poll_once_detects_a_second_focus_change() -> None:
    """Two distinct focus changes publish two distinct events."""
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    windows = iter([
        WindowInfo(title="Terminal", handle=1),
        WindowInfo(title="Browser", handle=2),
    ])

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window",
        staticmethod(lambda: next(windows)),
    ), patch.object(
        WindowFocusWatcher, "_resolve_posix_focus_meta",
        staticmethod(lambda win: (1, "some.app")),
    ):
        await watcher._poll_once()
        await watcher._poll_once()

    assert len(received) == 2
    assert received[0].window_title == "Terminal"
    assert received[1].window_title == "Browser"


@pytest.mark.asyncio
async def test_posix_poll_once_routes_blocked_title_to_capture_blocked() -> None:
    """A privacy-blocked title publishes AwarenessCaptureBlocked, not FrameUpdated."""
    bus, manager, privacy = _make_components()
    blocked: list[AwarenessCaptureBlocked] = []
    updated: list[FrameUpdated] = []
    bus.subscribe(AwarenessCaptureBlocked, _async_collect(blocked))
    bus.subscribe(FrameUpdated, _async_collect(updated))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window",
        staticmethod(lambda: WindowInfo(title="Sparkasse Online-Banking", handle=9)),
    ), patch.object(
        WindowFocusWatcher, "_resolve_posix_focus_meta",
        staticmethod(lambda win: (55, "firefox")),
    ):
        await watcher._poll_once()

    assert len(blocked) == 1
    assert len(updated) == 0


@pytest.mark.asyncio
async def test_posix_poll_once_returns_false_on_no_window() -> None:
    """No usable window info → _poll_once reports a failure, no crash."""
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window", staticmethod(lambda: None),
    ):
        ok = await watcher._poll_once()
    assert ok is False


@pytest.mark.asyncio
async def test_posix_poll_once_returns_false_on_probe_exception() -> None:
    """A raising probe is a failure too, not a crash."""
    def _raises() -> WindowInfo | None:
        raise RuntimeError("simulated Quartz failure")

    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window", staticmethod(_raises),
    ):
        ok = await watcher._poll_once()
    assert ok is False


@pytest.mark.asyncio
async def test_posix_poll_loop_stops_after_max_consecutive_failures(monkeypatch) -> None:
    """The poll loop self-disables after consecutive empty probes instead of
    spinning forever."""
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(window_mod, "display_present", lambda: True)
    monkeypatch.setattr(window_mod, "is_wayland", lambda: False)
    monkeypatch.setattr(window_mod, "_POSIX_POLL_INTERVAL_S", 0.0)

    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window", staticmethod(lambda: None),
    ):
        await watcher.start()
        assert watcher._poll_task is not None
        await asyncio.wait_for(watcher._poll_task, timeout=5.0)

    # The loop returned on its own (self-disabled) rather than being cancelled.
    assert watcher._poll_task.done()
    assert not watcher._poll_task.cancelled()
    await watcher.stop()    # idempotent cleanup, no crash


@pytest.mark.asyncio
async def test_posix_poll_loop_keeps_going_after_a_success(monkeypatch) -> None:
    """An empty probe after a working one is "nothing is focused", not a failure.

    ``xdotool getactivewindow`` exits non-zero whenever no client window holds
    the X11 input focus — the user is on the desktop, a window just closed, a
    lock screen is up. Those are everyday states, and counting them meant 10 s
    on the wallpaper permanently killed focus tracking for the whole session
    while the log blamed a missing xdotool.
    """
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(window_mod, "display_present", lambda: True)
    monkeypatch.setattr(window_mod, "is_wayland", lambda: False)
    monkeypatch.setattr(window_mod, "_POSIX_POLL_INTERVAL_S", 0.0)

    # One usable window, then far more empty probes than the startup gate allows.
    target = window_mod._POSIX_POLL_MAX_FAILURES * 3
    reached = asyncio.Event()
    loop = asyncio.get_running_loop()
    state = {"calls": 0}

    def _probe() -> WindowInfo | None:
        # Runs on a worker thread (asyncio.to_thread) — signal back thread-safely.
        state["calls"] += 1
        if state["calls"] > target:
            loop.call_soon_threadsafe(reached.set)
        return WindowInfo(title="Terminal", handle=1, pid=5) if state["calls"] == 1 else None

    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window", staticmethod(_probe),
    ):
        await watcher.start()
        task = watcher._poll_task
        assert task is not None
        await asyncio.wait_for(reached.wait(), timeout=5.0)
        assert not task.done(), "the loop self-disabled on a healthy empty probe"
        await watcher.stop()


@pytest.mark.asyncio
async def test_posix_poll_loop_survives_a_raising_iteration(monkeypatch) -> None:
    """A single bad iteration must not end focus tracking silently.

    Only ``CancelledError`` was caught, so anything else killed the task with an
    unretrieved exception and no log line — the feature was simply off.
    """
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(window_mod, "_POSIX_POLL_INTERVAL_S", 0.0)

    real_poll_once = WindowFocusWatcher._poll_once
    state = {"calls": 0}

    async def _flaky_poll_once(self) -> bool:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("simulated iteration failure")
        return await real_poll_once(self)

    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    published = asyncio.Event()

    async def _collect(ev) -> None:
        received.append(ev)
        published.set()

    bus.subscribe(FrameUpdated, _collect)
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_poll_once", _flaky_poll_once,
    ), patch.object(
        WindowFocusWatcher, "_posix_foreground_window",
        staticmethod(lambda: WindowInfo(title="Terminal", handle=1, pid=5)),
    ):
        await watcher.start()
        task = watcher._poll_task
        assert task is not None
        # The loop only reaches a publish if it survived the raising iteration.
        await asyncio.wait_for(published.wait(), timeout=5.0)
        assert not task.done()
        await watcher.stop()

    assert [ev.window_title for ev in received] == ["Terminal"]


@pytest.mark.asyncio
async def test_posix_poll_once_retries_a_window_whose_emit_failed() -> None:
    """A failed publish must not mark the window as already reported.

    Committing the change-detection markers before the emit turned a transient
    tail failure into a permanent one: the next tick saw an unchanged focus and
    stayed quiet, so ``state.current_frame`` kept describing the PREVIOUS window
    until the user happened to switch again.
    """
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    attempts = {"n": 0}

    def _meta(win: WindowInfo) -> tuple[int, str]:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient psutil failure")
        return 123, "gnome-terminal-server"

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window",
        staticmethod(lambda: WindowInfo(title="Terminal", handle=42)),
    ), patch.object(
        WindowFocusWatcher, "_resolve_posix_focus_meta", staticmethod(_meta),
    ):
        assert await watcher._poll_once() is True    # probe healthy, emit failed
        assert received == []
        assert await watcher._poll_once() is True    # same window, retried

    assert len(received) == 1
    assert received[0].window_title == "Terminal"
    assert manager.state.current_frame is not None
    assert manager.state.current_frame.active_pid == 123


# ---- Linux X11 pid resolution -----------------------------------------------


def test_linux_window_pid_returns_zero_without_xdotool(monkeypatch) -> None:
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: None)
    assert window_mod._linux_window_pid(7) == 0


def test_linux_window_pid_reads_the_reported_pid(monkeypatch) -> None:
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: "/usr/bin/xdotool")
    monkeypatch.setattr(
        window_mod.subprocess, "run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="4242\n", stderr=""),
    )
    assert window_mod._linux_window_pid(7) == 4242


def test_linux_window_pid_uses_the_resolved_executable(monkeypatch) -> None:
    """Spawn the binary ``which`` found, not a bare name re-resolved via PATH."""
    seen: list[list[str]] = []
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: "/opt/bin/xdotool")

    def _run(argv, **_kw):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="9\n", stderr="")

    monkeypatch.setattr(window_mod.subprocess, "run", _run)
    assert window_mod._linux_window_pid(7) == 9
    assert seen == [["/opt/bin/xdotool", "getwindowpid", "7"]]


def test_linux_window_pid_degrades_on_a_window_without_the_pid_property(
    monkeypatch,
) -> None:
    """xdotool exits non-zero for a client that publishes no ``_NET_WM_PID``.

    Routine, not an error — it must not surface as a resolution failure with a
    traceback that buries the real ones.
    """
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: "/usr/bin/xdotool")
    monkeypatch.setattr(
        window_mod.subprocess, "run",
        lambda *_a, **_kw: SimpleNamespace(
            returncode=1, stdout="", stderr="xdotool: window has no pid property",
        ),
    )
    assert window_mod._linux_window_pid(7) == 0


def test_linux_window_pid_degrades_on_unusable_output(monkeypatch) -> None:
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: "/usr/bin/xdotool")
    for stdout in ("", "not-a-pid", "-3"):
        monkeypatch.setattr(
            window_mod.subprocess, "run",
            lambda *_a, _out=stdout, **_kw: SimpleNamespace(
                returncode=0, stdout=_out, stderr="",
            ),
        )
        assert window_mod._linux_window_pid(7) == 0


def test_linux_window_pid_degrades_when_the_probe_times_out(monkeypatch) -> None:
    """A hung X connection must not propagate out of a poll-loop worker thread."""
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: "/usr/bin/xdotool")

    def _timeout(*_a, **_kw):
        raise window_mod.subprocess.TimeoutExpired(cmd="xdotool", timeout=5.0)

    monkeypatch.setattr(window_mod.subprocess, "run", _timeout)
    assert window_mod._linux_window_pid(7) == 0


def test_posix_focus_meta_never_reports_a_negative_pid(monkeypatch) -> None:
    """A negative pid looks resolved but names no process."""
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: "/usr/bin/xdotool")
    monkeypatch.setattr(
        window_mod.subprocess, "run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="-1", stderr=""),
    )
    assert WindowFocusWatcher._resolve_posix_focus_meta(
        WindowInfo(title="x", handle=5),
    ) == (0, "")


@pytest.mark.asyncio
async def test_posix_stop_without_start_is_noop() -> None:
    """stop() without a prior start() is a no-op on the POSIX path too."""
    bus, manager, privacy = _make_components()
    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    await watcher.stop()
    await watcher.stop()


@pytest.mark.asyncio
async def test_posix_poll_once_treats_a_titleless_window_as_a_healthy_probe() -> None:
    """A window with no readable title is not a dead backend.

    X11 windows legitimately carry no name, and xdotool's getwindowname can
    fail for one window while getactivewindow keeps working. Counting that as
    a failure meant five such polls (10 s) permanently disabled focus tracking
    for the rest of the session. Nothing is published either — an empty title
    is unidentifiable and must never be handed to the privacy filter.
    """
    bus, manager, privacy = _make_components()
    updated: list[FrameUpdated] = []
    blocked: list[AwarenessCaptureBlocked] = []
    bus.subscribe(FrameUpdated, _async_collect(updated))
    bus.subscribe(AwarenessCaptureBlocked, _async_collect(blocked))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window",
        staticmethod(lambda: WindowInfo(title="   ", handle=7)),
    ):
        assert await watcher._poll_once() is True
        assert await watcher._poll_once() is True

    assert updated == []
    assert blocked == []


@pytest.mark.asyncio
async def test_stop_discards_queued_frames_and_stale_focus_state(monkeypatch) -> None:
    """A restart must not replay pre-stop frames, and must not swallow the
    first genuine frame after it as "unchanged".

    Queue and change-detection markers live on the instance, so both used to
    survive a stop/start cycle (awareness toggled off and on in Settings):
    stale payloads were published as fresh frames, and the poller then saw the
    unchanged foreground window as "already reported" and emitted nothing —
    leaving ``state.current_frame`` frozen until the user switched windows.
    """
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "darwin")
    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window",
        staticmethod(lambda: WindowInfo(title="Terminal", handle=42)),
    ), patch.object(
        WindowFocusWatcher, "_resolve_posix_focus_meta",
        staticmethod(lambda win: (123, "Terminal.app")),
    ):
        watcher._started = True
        await watcher._poll_once()
        watcher._safe_enqueue((time.time_ns(), 4242))    # frame captured pre-stop

        await watcher.stop()
        assert watcher._queue.empty()
        assert watcher._poll_last_handle is None
        assert watcher._poll_last_title == ""
        assert watcher._last_hwnd == 0

        watcher._started = True
        await watcher._poll_once()    # same window, fresh run → must re-emit

    assert len(received) == 2


@pytest.mark.asyncio
async def test_posix_focus_meta_uses_the_pid_the_probe_already_resolved(
    monkeypatch,
) -> None:
    """Title and process identity must come from the SAME instant.

    ``window_state.foreground_window`` fills ``WindowInfo.pid`` from the very
    window it read the title off. Asking "who is frontmost NOW" a second time
    instead paired app A's title with app B's process whenever focus moved in
    between — a 2 s poll leaves that gap wide open — and the PrivacyFilter then
    judged a title against the wrong process name in both directions.
    """
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "darwin")

    def _must_not_run():
        raise AssertionError("re-queried the frontmost app instead of using win.pid")

    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: _must_not_run())

    pid, _name = WindowFocusWatcher._resolve_posix_focus_meta(
        WindowInfo(title="Notes", handle=7, pid=4242),
    )

    assert pid == 4242


@pytest.mark.asyncio
async def test_posix_focus_meta_degrades_to_zero_without_a_probe_pid(
    monkeypatch,
) -> None:
    """No pid from the probe and no usable backend → (0, ""), never a raise."""
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: None)

    assert WindowFocusWatcher._resolve_posix_focus_meta(
        WindowInfo(title="untitled", handle=9),
    ) == (0, "")


@pytest.mark.asyncio
async def test_posix_poll_emits_the_probe_pid_end_to_end(monkeypatch) -> None:
    """The published frame carries the probe's own pid, not a re-query."""
    monkeypatch.setattr(window_mod, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(window_mod.shutil, "which", lambda _name: None)

    bus, manager, privacy = _make_components()
    received: list[FrameUpdated] = []
    bus.subscribe(FrameUpdated, _async_collect(received))

    watcher = WindowFocusWatcher(manager=manager, privacy=privacy, bus=bus)
    with patch.object(
        WindowFocusWatcher, "_posix_foreground_window",
        staticmethod(lambda: WindowInfo(title="Terminal", handle=3, pid=777)),
    ):
        assert await watcher._poll_once() is True

    assert len(received) == 1
    assert received[0].pid == 777
