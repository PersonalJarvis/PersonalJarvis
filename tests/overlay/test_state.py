"""OverlayState — 8 States, stable wire-strings, GLOW_ACTIVE_STATES."""

from __future__ import annotations

from overlay.state import GLOW_ACTIVE_STATES, OverlayState


def test_eight_states() -> None:
    assert len(list(OverlayState)) == 8


def test_stable_string_values() -> None:
    expected = {
        OverlayState.IDLE: "idle",
        OverlayState.LISTENING: "listening",
        OverlayState.THINKING: "thinking",
        OverlayState.TYPING: "typing",
        OverlayState.CLICKING: "clicking",
        OverlayState.SPEAKING: "speaking",
        OverlayState.ERROR: "error",
        OverlayState.HIDDEN: "hidden",
    }
    for state, wire in expected.items():
        assert state.value == wire


def test_str_enum_inherits_string() -> None:
    # Wichtig fuer JSON-Serialisierung in Phase 9.2.
    assert isinstance(OverlayState.IDLE.value, str)
    assert OverlayState.IDLE == "idle"


def test_glow_active_only_in_typing_clicking() -> None:
    # Plan §6.1 — Glow ist NUR in TYPING und CLICKING aktiv.
    assert GLOW_ACTIVE_STATES == frozenset(
        {OverlayState.TYPING, OverlayState.CLICKING}
    )
