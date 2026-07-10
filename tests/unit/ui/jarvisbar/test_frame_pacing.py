"""Frame-pacing regression tests (stutter fix, 2026-07-10).

A 41s GIF capture of the bar frame-diffed to only ~5 visual updates/second on
average, in a burst-then-freeze pattern. Measured root causes (see the
constants' docstrings in ``overlay.py``):

1. Every branch ``renderer.render()`` can reach while the coarse mode is
   "idle" is time-independent (empty resting pill / hovered idle pill's
   static mic glyph / muted idle pill's static mic glyph — the equalizer
   bars and the close-X both require an active/listen/speak/think mode). So
   once its eased size has settled, re-rendering + re-blitting it every tick
   was pure waste — a real per-tick Windows DWM compositing cost paid for a
   byte-identical image. ``_schedule_frame`` now skips the render/PhotoImage/
   itemconfig work once idle has visibly settled (``_IDLE_SETTLE_TICKS``),
   regardless of hover/mute.
2. The re-arm delay is now adaptive (derived from actual tick cost via
   ``TARGET_FRAME_MS``/``MIN_FRAME_DELAY_MS``) instead of a blind constant, so
   an unusually slow render can't compound into an even longer visible gap.

These tests pin both contracts headless — a fake root/canvas/renderer, no
real Tk display, so they run on every OS including a headless CI box.
"""
from __future__ import annotations

import pytest

from jarvis.ui.jarvisbar.overlay import (
    _IDLE_SETTLE_TICKS,
    MIN_FRAME_DELAY_MS,
    TARGET_FRAME_MS,
    JarvisBarOverlay,
)


@pytest.fixture(autouse=True)
def _no_real_tk_photoimage(monkeypatch):
    """``_schedule_frame`` does a local ``from PIL import ImageTk`` and calls
    ``ImageTk.PhotoImage(img)``, which needs a live Tk default root. These
    tests run fully headless (fake root/canvas, no real Tk window), so
    ``PhotoImage`` is replaced with a passthrough — the tests care about
    render()/re-arm CALL COUNTS, not actual Tk image objects. Patching the
    real ``PIL.ImageTk`` module attribute (not ``overlay``'s local name)
    because the import is re-resolved fresh inside the function body."""
    monkeypatch.setattr("PIL.ImageTk.PhotoImage", lambda img: img)


class _FakeRoot:
    def __init__(self) -> None:
        self.scheduled: list[tuple[int, object]] = []

    def after(self, ms: int, fn: object) -> None:
        self.scheduled.append((ms, fn))


class _FakeCanvas:
    def __init__(self) -> None:
        self.itemconfig_calls = 0
        self.create_image_calls = 0

    def create_image(self, *a: object, **k: object) -> int:
        self.create_image_calls += 1
        return 1

    def itemconfig(self, *a: object, **k: object) -> None:
        self.itemconfig_calls += 1


class _CountingRenderer:
    """A renderer that returns a trivial sentinel and counts invocations,
    optionally simulating a slow tick via a monotonic clock stub."""

    def __init__(self) -> None:
        self.calls = 0

    def render(self, *a: object, **k: object) -> str:  # noqa: ANN201
        self.calls += 1
        return "sentinel-image"


def _bare_bar(
    renderer_obj: object, *, mode: str = "idle"
) -> tuple[JarvisBarOverlay, _FakeRoot, _FakeCanvas]:
    bar = JarvisBarOverlay.__new__(JarvisBarOverlay)
    bar._running = True
    bar._mode = mode
    bar._ext_level = 0.0
    bar._last_audible_t = 0.0
    bar._t0 = 0.0
    bar._hovered = False
    bar._muted = False
    bar._image_id = None
    bar._photo = None
    bar._last_frame_ns = 0
    bar._static_tick_key = None
    bar._static_tick_count = 0
    root = _FakeRoot()
    canvas = _FakeCanvas()
    bar._root = root
    bar._canvas = canvas
    bar._renderer = renderer_obj  # type: ignore[assignment]
    return bar, root, canvas


def test_idle_settled_pill_skips_render_after_enough_static_ticks():
    """A pill that stays idle/not-hovered/not-muted for _IDLE_SETTLE_TICKS
    consecutive frames must stop calling render()/repainting — the resting
    pill never changes once its eased size has settled, so repeating that
    work is pure waste (and the measured cause of the DWM compositing tax
    paid on every tick even at rest)."""
    renderer_obj = _CountingRenderer()
    bar, _root, canvas = _bare_bar(renderer_obj, mode="idle")

    # The first _IDLE_SETTLE_TICKS ticks must still render (settling window).
    for _ in range(_IDLE_SETTLE_TICKS):
        bar._schedule_frame()
    ticks_rendered_during_settle = renderer_obj.calls
    assert ticks_rendered_during_settle > 0, "idle pill never rendered even once"

    # From here on, the pill is fully settled — further ticks must NOT render.
    calls_before = renderer_obj.calls
    itemconfig_before = canvas.itemconfig_calls
    for _ in range(10):
        bar._schedule_frame()
    assert renderer_obj.calls == calls_before, (
        "settled idle pill kept calling render() — the skip optimization did not engage"
    )
    assert canvas.itemconfig_calls == itemconfig_before, (
        "settled idle pill kept repainting the canvas"
    )


def test_idle_settled_pill_still_reschedules_itself():
    """Skipping the render must NOT skip re-arming — the heartbeat/self-heal
    contract (watchdog, AP-19 guard) must stay intact even on a skipped tick."""
    renderer_obj = _CountingRenderer()
    bar, root, _canvas = _bare_bar(renderer_obj, mode="idle")

    for _ in range(_IDLE_SETTLE_TICKS + 5):
        bar._schedule_frame()

    assert root.scheduled, "frame loop stopped re-arming while skipping idle renders"
    last_heartbeat = bar._last_frame_ns
    assert last_heartbeat > 0, "heartbeat was not stamped on a skipped (idle-settled) tick"


def test_mode_change_resets_the_idle_settle_counter_and_renders_immediately():
    """A real transition (idle -> listen) must always render on the very next
    tick, even if the bar had just settled into the idle skip — the skip must
    never delay a genuine state change from becoming visible."""
    renderer_obj = _CountingRenderer()
    bar, _root, _canvas = _bare_bar(renderer_obj, mode="idle")

    for _ in range(_IDLE_SETTLE_TICKS + 5):
        bar._schedule_frame()
    calls_while_settled = renderer_obj.calls

    bar._mode = "listen"
    bar._ext_level = 0.5
    bar._schedule_frame()

    assert renderer_obj.calls == calls_while_settled + 1, (
        "a mode transition away from idle did not render on the next tick"
    )


def test_hovered_idle_pill_also_settles_and_skips():
    """A hovered idle pill only ever draws the static mic glyph (the close-X
    and equalizer bars require an active/listen/speak mode, unreachable while
    ``effective_mode == "idle"``) — so it is just as time-independent as the
    empty resting pill once its (larger, OPEN-sized) eased pill has settled,
    and must also stop repainting."""
    renderer_obj = _CountingRenderer()
    bar, _root, canvas = _bare_bar(renderer_obj, mode="idle")
    bar._hovered = True

    for _ in range(_IDLE_SETTLE_TICKS):
        bar._schedule_frame()
    calls_before = renderer_obj.calls
    itemconfig_before = canvas.itemconfig_calls

    for _ in range(10):
        bar._schedule_frame()

    assert renderer_obj.calls == calls_before, (
        "a settled hovered-idle pill kept calling render() — the static mic "
        "glyph never changes once the pill size has settled"
    )
    assert canvas.itemconfig_calls == itemconfig_before


def test_hover_flip_while_idle_forces_an_immediate_render():
    """A hover state CHANGE (mouse entering/leaving a settled idle pill) must
    always render on the very next tick — the skip must never delay a real
    interaction cue (showing/hiding the mic glyph) from appearing."""
    renderer_obj = _CountingRenderer()
    bar, _root, _canvas = _bare_bar(renderer_obj, mode="idle")

    for _ in range(_IDLE_SETTLE_TICKS + 5):
        bar._schedule_frame()
    calls_while_settled = renderer_obj.calls

    bar._hovered = True
    bar._schedule_frame()

    assert renderer_obj.calls == calls_while_settled + 1, (
        "a hover flip on a settled idle pill did not render on the next tick"
    )


def test_active_mode_never_skips_render():
    """listen/speak/think are always animating (level-driven bars or the
    orbital core) — the settle counter must never suppress their render()."""
    renderer_obj = _CountingRenderer()
    bar, _root, _canvas = _bare_bar(renderer_obj, mode="think")

    for _ in range(_IDLE_SETTLE_TICKS + 5):
        bar._schedule_frame()

    assert renderer_obj.calls == _IDLE_SETTLE_TICKS + 5, (
        "an active (thinking) pill skipped rendering — animation would freeze"
    )


class _SlowRenderer:
    """Simulates an unusually expensive render (e.g. the measured "think"+
    hovered outlier, up to ~30ms) via a monotonic-clock stub injected by the
    test, so the pacing math can be exercised deterministically."""

    def __init__(self, clock: list[float], cost_s: float) -> None:
        self._clock = clock
        self._cost_s = cost_s

    def render(self, *a: object, **k: object) -> str:  # noqa: ANN201
        self._clock[0] += self._cost_s
        return "sentinel-image"


def test_adaptive_pacing_shortens_the_next_delay_after_a_slow_tick(monkeypatch):
    """A tick that overruns TARGET_FRAME_MS must schedule the next one sooner
    than a full extra TARGET_FRAME_MS later — otherwise a single slow render
    compounds into an even longer visible gap (the old fixed-16ms behavior)."""
    clock = [1000.0]
    monkeypatch.setattr(
        "jarvis.ui.jarvisbar.overlay.time.perf_counter", lambda: clock[0]
    )
    slow_cost_s = (TARGET_FRAME_MS + 25) / 1000.0  # well past the target
    bar, root, _canvas = _bare_bar(_SlowRenderer(clock, slow_cost_s), mode="think")

    bar._schedule_frame()

    ms, _fn = root.scheduled[-1]
    assert ms == MIN_FRAME_DELAY_MS, (
        f"an overrun tick did not floor the next delay (got {ms}ms) — "
        "a slow frame would compound into a longer gap"
    )


def test_adaptive_pacing_keeps_the_full_target_delay_for_a_fast_tick(monkeypatch):
    """A negligible-cost tick must schedule (at, or very near) the full
    TARGET_FRAME_MS — the adaptive math must not change normal-case behavior."""
    clock = [1000.0]
    monkeypatch.setattr(
        "jarvis.ui.jarvisbar.overlay.time.perf_counter", lambda: clock[0]
    )
    bar, root, _canvas = _bare_bar(_CountingRenderer(), mode="think")

    bar._schedule_frame()

    ms, _fn = root.scheduled[-1]
    assert ms == TARGET_FRAME_MS
