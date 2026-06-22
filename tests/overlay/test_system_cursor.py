"""System-cursor lifecycle + session_bracket.

The Win32 + Pillow side is exercised by the live restart / demo; here we
test only the pure lifecycle (activate-once, idle-restore, shutdown safety,
session-bracket entry/exit semantics) with injected ``activate_fn`` /
``restore_fn`` / scheduler, so it runs everywhere — including the headless
CI box this project's cloud-first doctrine targets.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from jarvis.overlay.system_cursor import JarvisSystemCursor


def _rec():
    calls: list[int] = []
    return calls, lambda: calls.append(1)


class _FakeTimer:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def _make_scheduler():
    """Capture (delay, callback) pairs and return cancellable handles."""
    scheduled: list[tuple[int, callable, _FakeTimer]] = []

    def schedule(ms: int, cb):
        t = _FakeTimer()
        scheduled.append((ms, cb, t))
        return t

    return scheduled, schedule


# ---------------------------------------------------------------------------
# JarvisSystemCursor lifecycle
# ---------------------------------------------------------------------------


def test_ping_activates_when_inactive() -> None:
    acts, activate = _rec()
    restores, restore = _rec()
    _, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=activate, restore_fn=restore, schedule_after=schedule)
    c.ping()
    assert len(acts) == 1
    assert len(restores) == 0


def test_second_ping_does_not_reactivate() -> None:
    acts, activate = _rec()
    _, restore = _rec()
    _, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=activate, restore_fn=restore, schedule_after=schedule)
    c.ping()
    c.ping()
    assert len(acts) == 1  # cursor was already Jarvis-themed; no second swap


def test_ping_reschedules_idle_timer() -> None:
    _, activate = _rec()
    _, restore = _rec()
    scheduled, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=activate, restore_fn=restore, schedule_after=schedule)
    c.ping()
    c.ping()
    assert len(scheduled) == 2
    assert scheduled[0][2].cancelled is True
    assert scheduled[1][2].cancelled is False


def test_idle_fire_restores_default_cursor() -> None:
    _, activate = _rec()
    restores, restore = _rec()
    _, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=activate, restore_fn=restore, schedule_after=schedule)
    c.ping()
    c._idle_fire()
    assert len(restores) == 1


def test_idle_fire_after_restore_is_noop() -> None:
    _, activate = _rec()
    restores, restore = _rec()
    _, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=activate, restore_fn=restore, schedule_after=schedule)
    c.ping()
    c._idle_fire()
    c._idle_fire()
    assert len(restores) == 1  # never restore twice (would visibly flicker)


def test_ping_after_idle_reactivates() -> None:
    acts, activate = _rec()
    _, restore = _rec()
    _, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=activate, restore_fn=restore, schedule_after=schedule)
    c.ping()
    c._idle_fire()
    c.ping()
    assert len(acts) == 2  # Jarvis is back at the wheel; new swap


def test_shutdown_restores_when_active() -> None:
    _, activate = _rec()
    restores, restore = _rec()
    _, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=activate, restore_fn=restore, schedule_after=schedule)
    c.ping()
    c.shutdown()
    assert len(restores) == 1


def test_shutdown_when_inactive_is_noop() -> None:
    _, activate = _rec()
    restores, restore = _rec()
    _, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=activate, restore_fn=restore, schedule_after=schedule)
    c.shutdown()
    assert len(restores) == 0


def test_activate_failure_keeps_state_inactive() -> None:
    def boom() -> None:
        raise RuntimeError("CreateIconIndirect failed")

    restores, restore = _rec()
    _, schedule = _make_scheduler()
    c = JarvisSystemCursor(activate_fn=boom, restore_fn=restore, schedule_after=schedule)
    c.ping()
    assert c._active is False
    c.shutdown()
    assert restores == []  # nothing to restore — activation never succeeded


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 HCURSOR build is Windows-only")
def test_create_hcursor_does_not_overflow_on_64bit_handles() -> None:
    """Regression (2026-06-22): the gdi32/user32 handle args were passed to
    ctypes WITHOUT ``argtypes``, so on 64-bit Windows a >2^31 HBITMAP overflowed
    the default ``c_int`` marshalling ("OverflowError: int too long to convert")
    at the cleanup ``DeleteObject``. ``_real_activate`` therefore raised on EVERY
    Computer-Use mission, the failure was swallowed at DEBUG by
    ``JarvisSystemCursor.ping``, and the user kept their default cursor instead of
    the gold Jarvis one. Building the cursor must NOT raise and must return a
    real non-zero HCURSOR. (Builds + destroys an icon only — does NOT swap the
    live system cursor, so it is safe to run in the suite.)"""
    import ctypes
    from ctypes import wintypes

    from jarvis.overlay.system_cursor import _create_hcursor_from_rgba

    rgba = bytes(48 * 48 * 4)  # synthetic transparent RGBA; no Pillow needed
    hcur = _create_hcursor_from_rgba(
        rgba, width=48, height=48, hotspot_x=2, hotspot_y=2,
    )
    assert hcur != 0, "HCURSOR build returned 0 (DIB / CreateIconIndirect failed)"

    # Clean up the HICON/HCURSOR we just built (argtypes pinned for the same
    # 64-bit-handle reason the fix is about).
    user32 = ctypes.windll.user32
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.DestroyIcon(hcur)


def test_ping_jarvis_cursor_helper_is_safe_when_none_set() -> None:
    from jarvis.overlay.system_cursor import (
        ping_jarvis_cursor,
        set_jarvis_system_cursor,
    )

    set_jarvis_system_cursor(None)
    ping_jarvis_cursor()  # must not raise


# ---------------------------------------------------------------------------
# session_bracket — activate at start of a Computer-Use mission, restore at end
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.pings = 0
        self.shutdowns = 0

    def ping(self) -> None:
        self.pings += 1

    def shutdown(self) -> None:
        self.shutdowns += 1


def test_session_bracket_activates_at_entry_and_restores_at_exit() -> None:
    from jarvis.overlay.system_cursor import (
        session_bracket,
        set_jarvis_system_cursor,
    )

    rec = _Recorder()
    set_jarvis_system_cursor(rec)
    try:

        async def run() -> None:
            async with session_bracket():
                # Mid-session: cursor is Jarvis from the very first moment of
                # the mission, before the agent even takes its first
                # screenshot. No 3-5 s window with the default cursor.
                assert rec.pings == 1
                assert rec.shutdowns == 0
            # On exit: explicit restore — instant, no 30 s idle wait.
            assert rec.shutdowns == 1

        asyncio.run(run())
    finally:
        set_jarvis_system_cursor(None)


def test_session_bracket_restores_even_on_exception() -> None:
    from jarvis.overlay.system_cursor import (
        session_bracket,
        set_jarvis_system_cursor,
    )

    rec = _Recorder()
    set_jarvis_system_cursor(rec)
    try:

        async def run() -> None:
            with pytest.raises(RuntimeError):
                async with session_bracket():
                    raise RuntimeError("agent crashed mid-mission")

        asyncio.run(run())
        # Crash must still leave the user's cursor restored.
        assert rec.shutdowns == 1
    finally:
        set_jarvis_system_cursor(None)


def test_session_bracket_is_noop_when_no_cursor_installed() -> None:
    from jarvis.overlay.system_cursor import (
        session_bracket,
        set_jarvis_system_cursor,
    )

    set_jarvis_system_cursor(None)  # headless / no display

    async def run() -> None:
        async with session_bracket():
            pass  # must not raise

    asyncio.run(run())  # no exception expected


def test_session_bracket_logs_entry_and_exit(caplog) -> None:
    # Operators need a log breadcrumb confirming session-bracket activation
    # for every mission, because the user can't see the SetSystemCursor swap
    # in the log (the swap is silent — only the activate call logs at debug
    # on failure). An info-level entry+exit makes "did my cursor system fire
    # at mission start?" verifiable without running the GUI.
    from jarvis.overlay.system_cursor import (
        session_bracket,
        set_jarvis_system_cursor,
    )

    rec = _Recorder()
    set_jarvis_system_cursor(rec)
    try:

        async def run() -> None:
            with caplog.at_level("INFO", logger="jarvis.overlay.system_cursor"):
                async with session_bracket():
                    pass

        asyncio.run(run())
        messages = [r.getMessage() for r in caplog.records]
        assert any("session_bracket entered" in m for m in messages)
        assert any("session_bracket exited" in m for m in messages)
    finally:
        set_jarvis_system_cursor(None)


def test_session_bracket_survives_a_broken_cursor() -> None:
    # If the registered cursor's ping/shutdown blows up, the bracket must NOT
    # let the exception escape into the loop — a broken overlay never breaks
    # the mission. Same defence as the existing ping_jarvis_cursor helper.
    from jarvis.overlay.system_cursor import (
        session_bracket,
        set_jarvis_system_cursor,
    )

    class _Broken:
        def ping(self) -> None:
            raise RuntimeError("ping boom")

        def shutdown(self) -> None:
            raise RuntimeError("shutdown boom")

    set_jarvis_system_cursor(_Broken())
    try:

        async def run() -> None:
            async with session_bracket():
                pass  # must complete cleanly

        asyncio.run(run())  # no exception expected
    finally:
        set_jarvis_system_cursor(None)
