"""``show(mode)`` must MAP the window, not merely remember a mode string.

The dictation lane shipped broken once already in exactly this gap: every layer
agreed on the mode, every test asserted the mode, and the surface still put
nothing on screen. A mode is an intention; ``_do_show`` is the only thing that
turns it into pixels.

These tests drive the REAL ``JarvisBarOverlay`` with a stand-in root, so the
visibility decision is read out of the production code path rather than
restated. Everything below the queue (deiconify, layered attributes, z-order)
needs a live Tk window and is covered by ``test_show_lifts_topmost.py``; what is
pinned here is WHICH of the two terminal operations each call ends up choosing.

The fake surfaces used by the bridge's lifecycle tests model the same three
rules — mode vocabulary, startup gate, persistence — so this file is what keeps
those fakes honest about the surface they stand in for.
"""

from __future__ import annotations

import queue

import pytest

from jarvis.ui.jarvisbar import modes
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


def _headless_bar(*, persistent: bool, startup_gated: bool = False) -> JarvisBarOverlay:
    """A real bar with a stand-in root, so ``show`` reaches the UI queue.

    ``_tk_thread_id`` stays ``None`` so ``_enqueue_ui`` never mistakes the test
    thread for the Tk thread and runs the operation inline against a root that
    cannot service it.
    """
    bar = JarvisBarOverlay(
        persistent=persistent, accent="#e7c46e", startup_gated=startup_gated
    )
    bar._root = object()
    bar._tk_thread_id = None
    bar._ui_queue = queue.Queue()
    return bar


def _drain(bar: JarvisBarOverlay) -> list[object]:
    out: list[object] = []
    while True:
        try:
            out.append(bar._ui_queue.get_nowait())
        except queue.Empty:
            return out


@pytest.mark.parametrize("mode", modes.MODES)
def test_a_persistent_bar_maps_the_window_for_every_mode(mode: str) -> None:
    """The always-on bar is never withdrawn — not even by ``idle``."""
    bar = _headless_bar(persistent=True)

    bar.show(mode)

    assert _drain(bar) == [bar._do_show]
    assert bar._mode == mode


@pytest.mark.parametrize("mode", [m for m in modes.MODES if m != "idle"])
def test_a_non_persistent_bar_maps_the_window_for_every_active_mode(mode: str) -> None:
    """Dictation and notice modes reveal a hidden bar exactly like a wake word.

    This is the property the maintainer actually feels: press the shortcut and
    the bar is THERE. A mode that only updated ``_mode`` would leave a
    non-persistent bar withdrawn with a perfectly correct internal state.
    """
    bar = _headless_bar(persistent=False)

    bar.show(mode)

    assert _drain(bar) == [bar._do_show]


def test_a_non_persistent_bar_withdraws_on_idle() -> None:
    bar = _headless_bar(persistent=False)

    bar.show("idle")

    assert _drain(bar) == [bar._do_hide]


@pytest.mark.parametrize("mode", modes.MODES)
def test_a_startup_gated_bar_stores_the_mode_and_stays_off_screen(mode: str) -> None:
    """AP-26: nothing may advertise a voice stack that is still warming.

    The mode is still tracked so the release shows the CURRENT one — a bar that
    reset itself to idle on release would drop a dictation that began during
    warm-up.
    """
    bar = _headless_bar(persistent=True, startup_gated=True)

    bar.show(mode)

    assert _drain(bar) == []
    assert bar._mode == mode


@pytest.mark.parametrize("mode", [m for m in modes.MODES if m != "idle"])
def test_releasing_the_gate_maps_the_window_in_the_stored_mode(mode: str) -> None:
    bar = _headless_bar(persistent=True, startup_gated=True)
    bar.show(mode)
    _drain(bar)

    assert bar.release_startup_gate() is True

    assert _drain(bar) == [bar._do_show]
    assert bar._mode == mode


def test_an_unknown_mode_neither_maps_nor_overwrites_the_current_one() -> None:
    bar = _headless_bar(persistent=True)
    bar.show("listen")
    _drain(bar)

    bar.show("bogus")

    assert _drain(bar) == []
    assert bar._mode == "listen"
