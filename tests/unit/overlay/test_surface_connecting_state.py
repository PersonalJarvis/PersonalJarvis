"""The orb needs a state for "accepted your call, cannot carry it yet".

Until this landed, the whole handshake window was rendered as ``listening``.
For a realtime provider that is a lie with teeth: the ChatGPT-subscription
transport documents a 15-25 s cold start and declares a 45 s handshake budget,
so the bar could claim to be listening for three quarters of a minute while the
provider had not accepted a single audio frame — indistinguishable from a
freeze, and the user talks into a buffer the whole time.

``connecting`` is deliberately VISIBLE (the point is that a slow open is
something the user can see) and maps to the orb's ``think`` visual, the nearest
thing the orb owns for "busy, not yet listening".
"""
from __future__ import annotations

from typing import Any

from jarvis.overlay.surface import (
    _ORB_STATE_TO_ORB_MODE,
    _VISIBLE_STATES,
    TkColorKeyOverlay,
)


class _FakeOrb:
    def __init__(self) -> None:
        self.shown: list[str] = []
        self.hidden = 0
        self.started = False

    def start_in_thread(self) -> None:
        self.started = True

    def show(self, mode: str = "idle", **_kwargs: Any) -> None:
        self.shown.append(mode)

    def hide(self) -> None:
        self.hidden += 1


def test_connecting_is_part_of_the_state_vocabulary():
    assert "connecting" in _ORB_STATE_TO_ORB_MODE


def test_connecting_is_visible():
    """A silent handshake must not look like an idle desktop."""
    assert "connecting" in _VISIBLE_STATES


def test_connecting_does_not_render_as_listening():
    """The whole point: it must not claim the user is being heard."""
    assert _ORB_STATE_TO_ORB_MODE["connecting"] != _ORB_STATE_TO_ORB_MODE["listening"]


def test_overlay_shows_the_orb_while_connecting():
    orb = _FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)
    surface.start()

    surface.set_state("connecting")

    assert orb.shown == ["think"]
    assert surface.is_visible() is True


def test_connecting_hands_over_to_listening():
    """The handshake completing is a visible transition, not a no-op."""
    orb = _FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)
    surface.start()

    surface.set_state("connecting")
    surface.set_state("listening")

    assert orb.shown == ["think", "listen"]
    assert surface.is_visible() is True


def test_existing_states_are_unchanged():
    """A vocabulary addition must not move any state that already worked."""
    assert _ORB_STATE_TO_ORB_MODE["idle"] == "idle"
    assert _ORB_STATE_TO_ORB_MODE["listening"] == "listen"
    assert _ORB_STATE_TO_ORB_MODE["thinking"] == "think"
    assert _ORB_STATE_TO_ORB_MODE["speaking"] == "speak"
    assert "idle" not in _VISIBLE_STATES
    assert "error" not in _VISIBLE_STATES
    assert "paused" not in _VISIBLE_STATES


def test_unknown_states_still_degrade_to_hidden_idle():
    orb = _FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)
    surface.start()

    surface.set_state("something-nobody-defined")

    assert orb.shown == []
    assert orb.hidden == 1
    assert surface.is_visible() is False
