"""Overlay images/fonts must belong to the Tcl interpreter that consumes them.

The macOS companion host first creates a withdrawn bootstrap ``Tk`` root so
Tk's ``TKApplication`` owns NSApp, then the selected bar/mascot creates its own
root.  Those are separate Tcl interpreters.  Pillow/tkinter resources created
without an explicit master silently attach to the bootstrap interpreter; using
them on the overlay canvas then raises ``image \"pyimageN\" does not exist``.

These tests are headless and platform-neutral: the fakes prove that both frame
loops and the mascot bubble pass their actual consumer as the resource master.
That keeps the fix active on macOS without changing Windows/Linux behaviour.
"""

from __future__ import annotations

from PIL import Image, ImageTk


class _Root:
    def __init__(self) -> None:
        self.scheduled: list[tuple[int, object]] = []

    def after(self, delay_ms: int, callback: object) -> None:
        self.scheduled.append((delay_ms, callback))


class _Canvas:
    def __init__(self) -> None:
        self.created_with: object | None = None

    def create_image(self, *_args: object, **kwargs: object) -> int:
        self.created_with = kwargs.get("image")
        return 1

    def itemconfig(self, *_args: object, **_kwargs: object) -> None: ...


class _Renderer:
    def render(self, *_args: object, **_kwargs: object) -> Image.Image:
        return Image.new("RGB", (4, 4), "magenta")


def test_jarvisbar_frame_binds_photo_to_its_overlay_root(monkeypatch) -> None:
    from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay

    root = _Root()
    canvas = _Canvas()
    masters: list[object] = []

    def _photo(image: Image.Image, **kwargs: object) -> object:
        assert image.size == (4, 4)
        masters.append(kwargs.get("master"))
        return object()

    monkeypatch.setattr(ImageTk, "PhotoImage", _photo)
    monkeypatch.setattr("jarvis.audio.level_tap.playback_active", lambda: False)

    bar = JarvisBarOverlay.__new__(JarvisBarOverlay)
    bar._running = True
    bar._root = root
    bar._canvas = canvas
    bar._renderer = _Renderer()
    bar._mode = "think"
    bar._ext_level = 0.0
    bar._last_audible_t = 0.0
    bar._t0 = 0.0
    bar._hovered = False
    bar._muted = False
    bar._mac_transparent = False
    bar._static_tick_key = None
    bar._static_tick_count = 0
    bar._image_id = None
    bar._photo = None
    bar._last_frame_ns = 0

    bar._schedule_frame()

    assert masters == [root]
    assert canvas.created_with is bar._photo
    assert root.scheduled, "the normal frame-loop re-arm must remain intact"


def test_mascot_frame_binds_photo_to_its_overlay_root(monkeypatch) -> None:
    from ui.orb.overlay import OrbOverlay

    root = _Root()
    canvas = _Canvas()
    masters: list[object] = []

    def _photo(image: Image.Image, **kwargs: object) -> object:
        assert image.size == (4, 4)
        masters.append(kwargs.get("master"))
        return object()

    monkeypatch.setattr(ImageTk, "PhotoImage", _photo)

    mascot = OrbOverlay.__new__(OrbOverlay)
    mascot._running = True
    mascot._root = root
    mascot._canvas = canvas
    mascot._renderer = _Renderer()
    mascot._mode = "listen"
    mascot._ext_level = 0.0
    mascot._t0 = 0.0
    mascot._mac_transparent = False
    mascot._image_id = None
    mascot._photo = None

    mascot._schedule_frame()

    assert masters == [root]
    assert canvas.created_with is mascot._photo
    assert root.scheduled, "the mascot animation loop must remain intact"


def test_mascot_bubble_binds_named_fonts_to_its_toplevel(monkeypatch) -> None:
    from ui.orb import overlay as orb_mod

    class _Top:
        def overrideredirect(self, _enabled: bool) -> None: ...

        def wm_attributes(self, *_args: object) -> None: ...

        def configure(self, **_kwargs: object) -> None: ...

        def withdraw(self) -> None: ...

    class _BubbleCanvas:
        def pack(self, **_kwargs: object) -> None: ...

    top = _Top()
    font_roots: list[object] = []

    monkeypatch.setattr(orb_mod.sys, "platform", "linux")
    monkeypatch.setattr(orb_mod.tk, "Toplevel", lambda _parent: top)
    monkeypatch.setattr(orb_mod.tk, "Canvas", lambda *_args, **_kwargs: _BubbleCanvas())
    monkeypatch.setattr(orb_mod, "_hide_tk_window_from_task_switcher", lambda _top: None)

    def _font(**kwargs: object) -> object:
        font_roots.append(kwargs.get("root"))
        return object()

    monkeypatch.setattr(orb_mod.tkfont, "Font", _font)

    bubble = orb_mod.OrbCommentBubble(parent=object(), orb_x=0, orb_y=0, orb_w=108, screen_w=1440)

    assert bubble._top is top
    assert font_roots == [top, top]
