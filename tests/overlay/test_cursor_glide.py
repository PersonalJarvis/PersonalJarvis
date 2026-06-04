"""glide_cursor — drives the real OS cursor along an eased path.

This is the piece that makes "the mouse visibly travels to where it clicks"
true. It is fully dependency-injected (set_pos / get_pos / notify / sleep) so
it can be tested without touching Win32 or a real display.
"""
from __future__ import annotations

from jarvis.overlay.virtual_cursor import glide_cursor


def _recorder():
    calls: list[tuple[int, int]] = []
    return calls, lambda x, y: calls.append((x, y))


def test_glide_moves_real_cursor_and_ends_on_target() -> None:
    moves, set_pos = _recorder()
    glide_cursor(
        200, 80, start=(0, 0), duration_s=0.1, hz=60,
        set_pos=set_pos, sleep=lambda _s: None,
    )
    assert moves[-1] == (200, 80)
    assert len(moves) > 1  # actually animated, not a teleport


def test_glide_notifies_overlay_for_every_point() -> None:
    moves, set_pos = _recorder()
    pings, notify = _recorder()
    glide_cursor(
        100, 0, start=(0, 0), duration_s=0.1, hz=60,
        set_pos=set_pos, notify=notify, sleep=lambda _s: None,
    )
    assert pings == moves  # overlay highlight tracks the real cursor exactly


def test_glide_instant_when_duration_zero_is_single_landing() -> None:
    moves, set_pos = _recorder()
    glide_cursor(
        50, 50, start=(0, 0), duration_s=0.0,
        set_pos=set_pos, sleep=lambda _s: None,
    )
    assert moves == [(50, 50)]


def test_glide_uses_get_pos_when_start_omitted() -> None:
    moves, set_pos = _recorder()
    glide_cursor(
        10, 10, get_pos=lambda: (10, 10), duration_s=0.2,
        set_pos=set_pos, sleep=lambda _s: None,
    )
    # Start == target: nothing to travel, lands once on the target.
    assert moves[-1] == (10, 10)


def test_glide_survives_a_raising_notify() -> None:
    moves, set_pos = _recorder()

    def boom(_x: int, _y: int) -> None:
        raise RuntimeError("overlay thread is dead")

    # A broken overlay must never stop the real cursor from reaching its
    # target — the click still has to land.
    glide_cursor(
        99, 1, start=(0, 0), duration_s=0.1, hz=60,
        set_pos=set_pos, notify=boom, sleep=lambda _s: None,
    )
    assert moves[-1] == (99, 1)


def test_glide_sleeps_between_frames_not_after_last() -> None:
    moves, set_pos = _recorder()
    sleeps: list[float] = []
    glide_cursor(
        100, 0, start=(0, 0), duration_s=0.1, hz=60,
        set_pos=set_pos, sleep=lambda s: sleeps.append(s),
    )
    # One sleep fewer than points: we never sleep after the final landing.
    assert len(sleeps) == len(moves) - 1
