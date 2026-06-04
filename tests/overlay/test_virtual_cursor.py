"""Virtual-cursor core: glide-path generator, no-op singleton, config gate.

These tests cover the display-independent core of the "Jarvis virtual mouse"
feature — the visible cursor indicator that glides to where Computer-Use
clicks. The Tk rendering window lives in ``ui/orb/virtual_cursor_window.py``
and is exercised by the smoke test, not here; everything testable without a
display is pinned down in this module.
"""
from __future__ import annotations

import pytest

from jarvis.overlay.virtual_cursor import (
    NullVirtualCursor,
    glide_points,
    get_virtual_cursor,
    pulse_state,
    set_virtual_cursor,
    virtual_cursor_enabled,
)


# ---------------------------------------------------------------------------
# glide_points — pure easing path generator
# ---------------------------------------------------------------------------

def test_glide_points_starts_and_ends_at_endpoints() -> None:
    pts = glide_points(0, 0, 100, 40, steps=10)
    assert pts[0] == (0, 0)
    assert pts[-1] == (100, 40)


def test_glide_points_returns_requested_count() -> None:
    pts = glide_points(0, 0, 100, 0, steps=7)
    assert len(pts) == 7


def test_glide_points_single_step_is_just_the_target() -> None:
    # A 1-step glide cannot animate — it must land exactly on the target so
    # the real OS click never misses.
    pts = glide_points(5, 5, 200, 90, steps=1)
    assert pts == [(200, 90)]


def test_glide_points_x_is_monotonic_non_decreasing_when_moving_right() -> None:
    pts = glide_points(0, 0, 100, 0, steps=12)
    xs = [x for x, _ in pts]
    assert xs == sorted(xs)


def test_glide_points_clamps_non_positive_steps_to_target() -> None:
    assert glide_points(1, 2, 3, 4, steps=0) == [(3, 4)]
    assert glide_points(1, 2, 3, 4, steps=-5) == [(3, 4)]


def test_glide_points_are_integers() -> None:
    pts = glide_points(0, 0, 99, 33, steps=9)
    assert all(isinstance(x, int) and isinstance(y, int) for x, y in pts)


# ---------------------------------------------------------------------------
# pulse_state — click-pulse animation math (expanding, fading ring)
# ---------------------------------------------------------------------------

def test_pulse_starts_small_and_opaque() -> None:
    radius, alpha = pulse_state(0, duration_ms=400, max_radius=40)
    assert radius == 0
    assert alpha == 1.0


def test_pulse_grows_and_fades_over_time() -> None:
    early = pulse_state(100, duration_ms=400, max_radius=40)
    late = pulse_state(300, duration_ms=400, max_radius=40)
    assert late[0] > early[0]      # radius expands
    assert late[1] < early[1]      # alpha fades


def test_pulse_reaches_max_radius_at_end() -> None:
    radius, _ = pulse_state(400, duration_ms=400, max_radius=40)
    assert radius == 40


def test_pulse_is_none_after_it_expires() -> None:
    assert pulse_state(401, duration_ms=400, max_radius=40) is None


# ---------------------------------------------------------------------------
# Singleton accessor + null object
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    set_virtual_cursor(None)
    yield
    set_virtual_cursor(None)


def test_default_cursor_is_null_object() -> None:
    cur = get_virtual_cursor()
    assert isinstance(cur, NullVirtualCursor)


def test_null_cursor_methods_never_raise() -> None:
    cur = get_virtual_cursor()
    # The null object must absorb every call so a missing display can never
    # break a real click (cloud-first / headless VPS doctrine).
    cur.show_move(10, 20)
    cur.show_click(10, 20, button="left", double=False)
    cur.show_path_point(10, 20)
    cur.clear()
    cur.shutdown()


def test_set_and_get_roundtrip() -> None:
    class _Fake(NullVirtualCursor):
        pass

    fake = _Fake()
    set_virtual_cursor(fake)
    assert get_virtual_cursor() is fake


# ---------------------------------------------------------------------------
# Config gate
# ---------------------------------------------------------------------------

def test_enabled_defaults_true_when_section_missing() -> None:
    assert virtual_cursor_enabled({}) is True


def test_enabled_reads_computer_use_flag() -> None:
    assert virtual_cursor_enabled({"computer_use": {"show_virtual_cursor": False}}) is False
    assert virtual_cursor_enabled({"computer_use": {"show_virtual_cursor": True}}) is True
