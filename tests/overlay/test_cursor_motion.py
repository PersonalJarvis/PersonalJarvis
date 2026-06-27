"""glide_os_cursor — thin Win32 adapter that glides the real cursor.

The win32 ``GetCursorPos`` / ``SetCursorPos`` calls and ``sleep`` are
injectable so the orchestration is testable off-Windows and without a real
cursor. It bridges :func:`glide_cursor` to the installed virtual-cursor
overlay (``get_virtual_cursor().show_path_point``).
"""
from __future__ import annotations

import pytest

from jarvis.control.cursor_motion import glide_os_cursor
from jarvis.overlay.virtual_cursor import NullVirtualCursor, set_virtual_cursor


class _RecordingCursor(NullVirtualCursor):
    def __init__(self) -> None:
        self.points: list[tuple[int, int]] = []

    def show_path_point(self, x: int, y: int) -> None:
        self.points.append((x, y))


@pytest.fixture(autouse=True)
def _reset_singleton():
    set_virtual_cursor(None)
    yield
    set_virtual_cursor(None)


def test_glide_os_cursor_lands_on_target() -> None:
    moves: list[tuple[int, int]] = []
    glide_os_cursor(
        300, 120,
        get_pos=lambda: (0, 0),
        set_pos=lambda x, y: moves.append((x, y)),
        sleep=lambda _s: None,
        duration_ms=100,
    )
    assert moves[-1] == (300, 120)


def test_glide_os_cursor_feeds_the_installed_overlay() -> None:
    rec = _RecordingCursor()
    set_virtual_cursor(rec)
    moves: list[tuple[int, int]] = []
    glide_os_cursor(
        100, 0,
        get_pos=lambda: (0, 0),
        set_pos=lambda x, y: moves.append((x, y)),
        sleep=lambda _s: None,
        duration_ms=100,
    )
    assert rec.points == moves  # highlight tracks the real cursor


def test_set_glide_ms_controls_default_duration() -> None:
    from jarvis.control import cursor_motion

    original = cursor_motion._resolve_glide_ms()
    try:
        cursor_motion.set_glide_ms(0)
        moves: list[tuple[int, int]] = []
        glide_os_cursor(
            7, 7,
            get_pos=lambda: (0, 0),
            set_pos=lambda x, y: moves.append((x, y)),
            sleep=lambda _s: None,
        )
        assert moves == [(7, 7)]  # 0 ms default => instant landing
    finally:
        cursor_motion.set_glide_ms(original)


def test_glide_os_cursor_zero_duration_is_instant() -> None:
    moves: list[tuple[int, int]] = []
    glide_os_cursor(
        42, 42,
        get_pos=lambda: (0, 0),
        set_pos=lambda x, y: moves.append((x, y)),
        sleep=lambda _s: None,
        duration_ms=0,
    )
    assert moves == [(42, 42)]


def test_win_set_pos_does_not_raise_on_unreliable_zero_return(monkeypatch) -> None:
    """Regression (CU clicks void on the LEFT monitor): SetCursorPos returns 0
    (reports failure) yet STILL moves the cursor when crossing into a monitor
    positioned left of primary — negative virtual-desktop X. The old
    ``if not SetCursorPos(): raise`` turned that spurious 0 into a hard error that
    aborted every left-monitor click. _win_set_pos must trust the move and NOT
    raise on the boolean."""
    from jarvis.control import cursor_motion

    calls: list[tuple[int, int]] = []

    def _fake_raw(x: int, y: int) -> int:
        calls.append((x, y))
        return 0  # SetCursorPos "failure" return — must be ignored

    monkeypatch.setattr(cursor_motion, "_raw_set_cursor", _fake_raw)
    cursor_motion._win_set_pos(-1280, 720)  # negative X, must not raise
    assert calls == [(-1280, 720)]
