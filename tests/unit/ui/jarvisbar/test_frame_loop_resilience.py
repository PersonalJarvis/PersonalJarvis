"""The JarvisBar animation loop must be self-healing.

Forensic: the bar "stopped moving / froze" until an app restart. Root cause —
``_schedule_frame`` re-arms the next frame ONLY from its tail line, so a single
transient render/Tk error (e.g. ``ImageTk.PhotoImage`` raising, a ``TclError``
during a window move) skips the re-arm and the loop dies permanently while the
Tk mainloop keeps running (window stays visible, frozen on its last frame).

These tests pin the contract: one frame error drops just that frame and the
loop keeps ticking. Headless — a fake root/canvas/renderer, no real Tk display.
"""
from __future__ import annotations

import time

from jarvis.ui.jarvisbar.overlay import (
    FRAME_STALL_THRESHOLD_NS,
    MIN_FRAME_DELAY_MS,
    TARGET_FRAME_MS,
    JarvisBarOverlay,
)


class _FakeRoot:
    def __init__(self) -> None:
        self.scheduled: list[tuple[int, object]] = []

    def after(self, ms: int, fn: object) -> None:
        self.scheduled.append((ms, fn))


class _FakeCanvas:
    def create_image(self, *a: object, **k: object) -> int:
        return 1

    def itemconfig(self, *a: object, **k: object) -> None:
        pass


class _BoomRenderer:
    """A renderer whose render() always raises — simulates a transient
    PIL/Tk hiccup mid-frame."""

    def render(self, *a: object, **k: object):  # noqa: ANN201
        raise RuntimeError("transient render/Tk hiccup")


def _bare_bar(renderer_obj: object) -> tuple[JarvisBarOverlay, _FakeRoot]:
    bar = JarvisBarOverlay.__new__(JarvisBarOverlay)
    bar._running = True
    bar._mode = "think"
    bar._ext_level = 0.0
    bar._last_audible_t = 0.0
    bar._t0 = 0.0
    bar._hovered = False
    bar._image_id = None
    bar._photo = None
    bar._last_frame_ns = 0
    bar._static_tick_key = None
    bar._static_tick_count = 0
    root = _FakeRoot()
    bar._root = root
    bar._canvas = _FakeCanvas()
    bar._renderer = renderer_obj  # type: ignore[assignment]
    return bar, root


def test_frame_loop_reschedules_after_render_error():
    """A render exception must NOT kill the loop: the next frame is still armed
    and the call itself does not propagate (the Tk mainloop must keep running).

    The re-arm delay is adaptive (derived from how long the tick actually
    took, see ``TARGET_FRAME_MS``/``MIN_FRAME_DELAY_MS``), so this asserts the
    delay stays within the documented bounds rather than a hardcoded 16 — a
    fast-failing render (like ``_BoomRenderer``) should land at (or very near)
    the full ``TARGET_FRAME_MS`` since almost no time elapsed."""
    bar, root = _bare_bar(_BoomRenderer())

    bar._schedule_frame()  # must not raise

    assert root.scheduled, "frame loop died: next frame was not rescheduled"
    ms, fn = root.scheduled[-1]
    assert fn == bar._schedule_frame
    assert MIN_FRAME_DELAY_MS <= ms <= TARGET_FRAME_MS


def test_frame_loop_stops_rearming_when_not_running():
    """The self-heal must not fight a deliberate stop(): once _running is False
    the loop returns early and does NOT re-arm."""
    bar, root = _bare_bar(_BoomRenderer())
    bar._running = False

    bar._schedule_frame()

    assert root.scheduled == []


def test_frame_loop_stamps_a_heartbeat_even_when_render_fails():
    """Every tick stamps ``_last_frame_ns`` so the watchdog can tell a live loop
    from a dead one — and it must stamp even when the render itself raised, since
    a dropped-but-rearmed frame is still a LIVING loop."""
    bar, _root = _bare_bar(_BoomRenderer())
    assert bar._last_frame_ns == 0

    bar._schedule_frame()  # render raises, finally must still stamp + re-arm

    assert bar._last_frame_ns > 0, "frame loop did not stamp its heartbeat"


def test_watchdog_revives_a_stalled_frame_loop():
    """The watchdog is the second, independent after-chain. When the frame loop's
    heartbeat goes stale (it died silently — the very class of bug the tail-only
    re-arm cannot recover from), the watchdog must kick the frame loop back to
    life."""
    bar, root = _bare_bar(_BoomRenderer())
    bar._last_frame_ns = time.monotonic_ns() - (FRAME_STALL_THRESHOLD_NS * 2)
    revived: list[bool] = []
    bar._schedule_frame = lambda: revived.append(True)  # type: ignore[method-assign]

    bar._schedule_frame_watchdog()

    assert revived == [True], "watchdog did not revive the stalled frame loop"
    assert any(
        fn == bar._schedule_frame_watchdog for _ms, fn in root.scheduled
    ), "watchdog did not re-arm itself"


def test_watchdog_does_not_revive_a_healthy_loop():
    """A fresh heartbeat means the loop is alive — the watchdog must NOT kick it
    again (no double frame-chains, no spurious revival). It still re-arms itself.
    This is the AP-19/BUG-032 guard: a continuously-stamped heartbeat cannot
    false-fire while the loop is actually ticking."""
    bar, root = _bare_bar(_BoomRenderer())
    bar._last_frame_ns = time.monotonic_ns()  # just ticked
    revived: list[bool] = []
    bar._schedule_frame = lambda: revived.append(True)  # type: ignore[method-assign]

    bar._schedule_frame_watchdog()

    assert revived == [], "watchdog falsely revived a healthy loop"
    assert any(fn == bar._schedule_frame_watchdog for _ms, fn in root.scheduled)


def test_watchdog_stops_when_not_running():
    """A deliberate stop() must end the watchdog too — once _running is False it
    returns early and does not re-arm."""
    bar, root = _bare_bar(_BoomRenderer())
    bar._running = False

    bar._schedule_frame_watchdog()

    assert root.scheduled == []
