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

from jarvis.ui.whisperbar.overlay import WhisperBarOverlay


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


def _bare_bar(renderer_obj: object) -> tuple[WhisperBarOverlay, _FakeRoot]:
    bar = WhisperBarOverlay.__new__(WhisperBarOverlay)
    bar._running = True
    bar._mode = "think"
    bar._ext_level = 0.0
    bar._last_audible_t = 0.0
    bar._t0 = 0.0
    bar._hovered = False
    bar._image_id = None
    bar._photo = None
    root = _FakeRoot()
    bar._root = root
    bar._canvas = _FakeCanvas()
    bar._renderer = renderer_obj  # type: ignore[assignment]
    return bar, root


def test_frame_loop_reschedules_after_render_error():
    """A render exception must NOT kill the loop: the next frame is still armed
    and the call itself does not propagate (the Tk mainloop must keep running)."""
    bar, root = _bare_bar(_BoomRenderer())

    bar._schedule_frame()  # must not raise

    assert root.scheduled, "frame loop died: next frame was not rescheduled"
    ms, fn = root.scheduled[-1]
    assert fn == bar._schedule_frame
    assert ms == 16


def test_frame_loop_stops_rearming_when_not_running():
    """The self-heal must not fight a deliberate stop(): once _running is False
    the loop returns early and does NOT re-arm."""
    bar, root = _bare_bar(_BoomRenderer())
    bar._running = False

    bar._schedule_frame()

    assert root.scheduled == []
