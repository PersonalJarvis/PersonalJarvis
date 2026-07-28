"""Edge semantics every non-Windows hotkey backend must share.

``HotkeyTrigger._build_bindings`` produces ``[combo, on_press, on_release]``
rows in exactly three shapes:

* **press-only** — ``on_release`` is ``None``.
* **release-only** — a TOGGLE binding (Call, Hangup, hands-free Dictation). The
  handler sits on ``on_release`` so a HELD key fires exactly once instead of
  storming on OS key repeat.
* **both edges** — push-to-talk: the down edge starts recording, the up edge
  submits it.

Each backend must run a handler on ITS OWN edge and nowhere else. The pynput and
Quartz backends used to fall back to ``on_release`` on the chord-DOWN
transition, so every toggle fired on key-down on macOS and Linux while the
Windows path fired on key-up — one config, two behaviours. These tests drive the
chord matcher of both backends through the identical scenarios, so a future edit
cannot re-open the divergence on one OS alone.
"""

from __future__ import annotations

import types
from collections.abc import Callable

import pytest

# The combo under test is ``alt + l`` on both backends.
_QUARTZ_FLAG_ALT = 1 << 19
_QUARTZ_KEYCODE_L = 0x25


def _pynput_backend():
    """A ``PynputBackend`` plus chord-down / chord-up drivers for ``alt + l``."""
    from jarvis.trigger.backends.pynput import PynputBackend

    backend = PynputBackend()
    # Linux-X11 reports the physical right Alt as ``alt_r``; letters arrive as a
    # ``KeyCode`` carrying ``.char``.
    alt = types.SimpleNamespace(char=None, name="alt_r")
    key_l = types.SimpleNamespace(char="l", name=None)

    def chord_down() -> None:
        backend._on_press_key(alt)
        backend._on_press_key(key_l)

    def chord_up() -> None:
        backend._on_release_key(key_l)
        backend._on_release_key(alt)

    return backend, chord_down, chord_up


def _quartz_backend():
    """A ``QuartzHotkeyBackend`` plus the same drivers, keyed by keycode+flags."""
    from jarvis.trigger.backends.quartz import QuartzHotkeyBackend

    backend = QuartzHotkeyBackend()
    backend._permission_check = lambda: True

    def chord_down() -> None:
        backend._handle_flags(_QUARTZ_FLAG_ALT)
        backend._handle_key_down(_QUARTZ_KEYCODE_L)

    def chord_up() -> None:
        backend._handle_key_up(_QUARTZ_KEYCODE_L)
        backend._handle_flags(0)

    return backend, chord_down, chord_up


_BACKENDS: dict[str, Callable[[], tuple[object, Callable[[], None], Callable[[], None]]]] = {
    "pynput": _pynput_backend,
    "quartz": _quartz_backend,
}


@pytest.fixture(params=sorted(_BACKENDS), ids=sorted(_BACKENDS))
def chord(request):
    """Yield ``(backend, chord_down, chord_up)`` for every non-Windows backend."""
    return _BACKENDS[request.param]()


def test_press_only_binding_fires_on_the_down_edge_only(chord) -> None:
    backend, chord_down, chord_up = chord
    edges: list[str] = []
    backend.register([["alt + l", lambda: edges.append("press"), None]])

    chord_down()
    assert edges == ["press"]
    chord_up()
    assert edges == ["press"]


def test_release_only_toggle_fires_on_the_up_edge_only(chord) -> None:
    """A toggle row (``on_press=None``) must NOT fire on the chord-down edge."""
    backend, chord_down, chord_up = chord
    edges: list[str] = []
    backend.register([["alt + l", None, lambda: edges.append("toggle")]])

    chord_down()
    assert edges == [], "a release-only toggle must stay silent on key-down"
    chord_up()
    assert edges == ["toggle"]


def test_both_edges_binding_keeps_push_to_talk_working(chord) -> None:
    backend, chord_down, chord_up = chord
    edges: list[str] = []
    backend.register(
        [["alt + l", lambda: edges.append("down"), lambda: edges.append("up")]]
    )

    chord_down()
    assert edges == ["down"]
    chord_up()
    assert edges == ["down", "up"]


def test_held_toggle_key_fires_exactly_once(chord) -> None:
    """OS key repeat re-delivers the down edge; the toggle still fires once."""
    backend, chord_down, chord_up = chord
    edges: list[str] = []
    backend.register([["alt + l", None, lambda: edges.append("toggle")]])

    chord_down()
    chord_down()  # key repeat
    chord_down()
    assert edges == []
    chord_up()
    assert edges == ["toggle"]


def test_a_row_without_any_handler_never_raises(chord) -> None:
    """A degenerate row must be inert on both edges, not an AttributeError."""
    backend, chord_down, chord_up = chord
    backend.register([["alt + l", None, None]])

    chord_down()
    chord_up()
    assert backend.received_any_event() is True
