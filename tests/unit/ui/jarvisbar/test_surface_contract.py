"""JarvisBarOverlay must expose the duck-typed surface OrbBusBridge drives,
and every method must be safe to call before/without a Tk window."""
from __future__ import annotations

from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay

# Every method OrbBusBridge invokes on the surface (directly or via getattr).
REQUIRED = [
    "show",
    "hide",
    "set_level",
    "play_animation",
    "stop_animation",
    "show_listening_transcript",
    "hide_comment",
    "start_mouth_animation",
    "stop_mouth_animation",
    "set_on_mute_toggle",
    "set_feedback_publisher",
    "set_on_show_window",
    "start_in_thread",
    "stop",
    "_on_reset_double_click",
]


def test_surface_exposes_every_method_the_bridge_calls():
    for name in REQUIRED:
        assert callable(getattr(JarvisBarOverlay, name, None)), name


def test_constructs_headless_without_tk():
    bar = JarvisBarOverlay(persistent=False, accent="#abcdef")
    assert bar._mode == "idle"
    assert bar._persistent is False
    assert bar._root is None


def test_methods_safe_without_tk_window():
    bar = JarvisBarOverlay.__new__(JarvisBarOverlay)
    bar._root = None
    bar._mode = "idle"
    bar._ext_level = 0.0
    bar._persistent = True
    bar._on_mute_toggle = None
    bar._feedback_publisher = None
    bar._level_unsub = None
    bar._running = False

    for mode in ("idle", "listen", "speak", "think"):
        bar.show(mode)
        assert bar._mode == mode
    bar.show("bogus")  # invalid mode ignored
    assert bar._mode == "think"

    bar.set_level(0.7)
    assert abs(bar._ext_level - 0.7) < 1e-9
    bar.set_level(5.0)
    assert bar._ext_level == 1.0
    bar.set_level(-2.0)
    assert bar._ext_level == 0.0

    bar.hide()  # _root None → safe no-op
    bar.play_animation("wave", x=1)
    bar.stop_animation("think")
    bar.show_listening_transcript("hi", 10)
    bar.hide_comment()
    bar.start_mouth_animation(5)
    bar.stop_mouth_animation()

    bar.set_on_mute_toggle(lambda: None)
    bar.set_feedback_publisher(lambda k, d: None)
    assert bar._on_mute_toggle is not None
    assert bar._feedback_publisher is not None

    bar._on_reset_double_click()  # _root None → safe no-op
    bar.stop()  # _level_unsub unset on a bare __new__ object → safe
