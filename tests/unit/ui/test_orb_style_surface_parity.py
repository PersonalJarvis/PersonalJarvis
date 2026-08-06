"""The voice orb wears the app's surface; the mascot keeps its own.

Reported 2026-08-06: on the desktop the procedural sphere spoke through the
mascot's comic bubble — black body, thick yellow border, bold yellow text —
which reads as a different program's window sitting on the same screen. The
look now travels with the style, and the sphere additionally carries the same
four controls its in-app twin has (``components/agentic/VoiceBubble.tsx``).

These are pure-logic guards: no Tk root is created, because the overlay's own
pytest guard refuses to put a real window on a developer's desktop.
"""

from __future__ import annotations

import pytest

from ui.orb import overlay

# --- the bubble -------------------------------------------------------------


def test_the_sphere_does_not_borrow_the_mascots_comic_bubble() -> None:
    orb = overlay.bubble_theme_for_style("voice_orb")
    mascot = overlay.bubble_theme_for_style("mascot")
    assert orb != mascot
    # The three things that made it look foreign next to the sphere.
    assert orb.text.lower() != overlay.BUBBLE_TEXT_HEX.lower()
    assert orb.border.lower() != overlay.BUBBLE_BORDER_HEX.lower()
    assert orb.bold is False
    assert orb.border_width < mascot.border_width


def test_the_sphere_has_no_speech_tail() -> None:
    """A tail points at a mouth. The sphere has none, and neither does the
    in-app bubble this copies."""
    assert overlay.bubble_theme_for_style("voice_orb").tail is False
    assert overlay.bubble_theme_for_style("mascot").tail is True


def test_the_ghost_keeps_its_bubble_exactly_as_it_was() -> None:
    mascot = overlay.bubble_theme_for_style("mascot")
    assert mascot.bg == overlay.BUBBLE_BG_HEX
    assert mascot.border == overlay.BUBBLE_BORDER_HEX
    assert mascot.text == overlay.BUBBLE_TEXT_HEX
    assert mascot.bold is True


def test_an_unknown_style_falls_back_to_the_mascot_bubble() -> None:
    assert overlay.bubble_theme_for_style("something-new") is overlay.MASCOT_BUBBLE_THEME


# --- the control row --------------------------------------------------------


def test_only_the_sphere_wears_the_control_row() -> None:
    """A row of discs under the cartoon ghost would read as a bolted-on toolbar."""
    assert overlay.OrbOverlay(style="voice_orb")._controls_wanted() is True
    assert overlay.OrbOverlay(style="mascot")._controls_wanted() is False


def test_the_mic_disc_starts_a_conversation_when_there_is_none() -> None:
    orb = overlay.OrbOverlay(style="voice_orb")
    calls: list[str] = []
    orb.set_on_talk(lambda: calls.append("talk"))
    orb.set_on_hangup(lambda: calls.append("hangup"))

    orb._on_control_action("mic")

    assert calls == ["talk"]


def test_the_mic_disc_hangs_up_a_running_conversation() -> None:
    orb = overlay.OrbOverlay(style="voice_orb")
    calls: list[str] = []
    orb.set_on_talk(lambda: calls.append("talk"))
    orb.set_on_hangup(lambda: calls.append("hangup"))
    orb._mode = "speak"

    orb._on_control_action("mic")

    assert calls == ["hangup"]
    # Local feedback immediately, rather than waiting for microphone teardown
    # plus the EventBus round-trip.
    assert orb._mode == "idle"


def test_the_x_ends_the_conversation_before_putting_the_orb_away() -> None:
    orb = overlay.OrbOverlay(style="voice_orb")
    calls: list[str] = []
    orb.set_on_hangup(lambda: calls.append("hangup"))
    orb._mode = "listen"

    orb._on_control_action("close")

    assert calls == ["hangup"]
    assert orb._mode == "idle"


def test_the_x_on_an_idle_orb_hangs_up_nothing() -> None:
    orb = overlay.OrbOverlay(style="voice_orb")
    calls: list[str] = []
    orb.set_on_hangup(lambda: calls.append("hangup"))

    orb._on_control_action("close")

    assert calls == []


def test_the_speaker_disc_delegates_when_a_host_owns_the_pipeline() -> None:
    """On macOS the orb runs in a companion process with no pipeline of its own."""
    orb = overlay.OrbOverlay(style="voice_orb")
    calls: list[str] = []
    orb.set_on_speaker_toggle(lambda: calls.append("speaker"))

    orb._on_control_action("speaker")
    assert calls == ["speaker"]
    assert orb._speaker_muted is True

    orb._on_control_action("speaker")
    assert calls == ["speaker", "speaker"]
    assert orb._speaker_muted is False


def test_the_speaker_disc_leaves_its_icon_alone_without_a_pipeline() -> None:
    """No pipeline, no mute — the disc must not claim one happened."""
    from jarvis.core import runtime_refs

    runtime_refs.set_speech_pipeline(None)
    orb = overlay.OrbOverlay(style="voice_orb")

    orb._on_control_action("speaker")

    assert orb._speaker_muted is False


@pytest.mark.parametrize("action", ["attach", "mic", "close", "speaker"])
def test_every_rendered_disc_has_a_handler(action: str) -> None:
    """The row draws four discs; a drawn disc that does nothing is a dead button."""
    assert action in overlay.orb_controls.ACTIONS
    orb = overlay.OrbOverlay(style="voice_orb")
    # No Tk root and no callbacks wired: every branch must survive that rather
    # than raising into the Tk event loop.
    orb._on_control_action(action)
