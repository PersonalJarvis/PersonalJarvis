"""The orb window paints the style it was asked for, and never a magenta box.

Both looks — the Gigi mascot and the procedural voice orb — share ONE frameless
always-on-top window, which is what makes the voice orb draggable to any
monitor instead of being trapped inside the app. These tests pin that seam:
style in, right renderer out, unknown values recovered rather than fatal, and a
desktop session without per-pixel transparency degrading to nothing visible
instead of an opaque key-coloured square.
"""
from __future__ import annotations

import tkinter as tk

import pytest

import ui.orb.overlay as overlay_mod
from jarvis.ui.overlay_styles import ORB_STYLES
from ui.orb.overlay import MascotRenderer, OrbOverlay, _apply_color_key, _coerce_style
from ui.orb.voice_orb import VoiceOrbRenderer


@pytest.fixture(autouse=True)
def _no_env_style(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_ORB_STYLE", raising=False)


def test_voice_orb_style_builds_the_voice_orb_renderer() -> None:
    overlay = OrbOverlay(style="voice_orb")

    assert overlay._style == "voice_orb"
    assert isinstance(overlay._build_renderer("voice_orb"), VoiceOrbRenderer)


def test_mascot_style_still_builds_the_mascot_renderer() -> None:
    overlay = OrbOverlay(style="mascot")

    assert overlay._style == "mascot"
    assert isinstance(overlay._build_renderer("mascot"), MascotRenderer)


def test_env_can_select_the_voice_orb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_ORB_STYLE", "voice_orb")

    assert OrbOverlay(style="mascot")._style == "voice_orb"


def test_style_switch_before_start_is_remembered() -> None:
    overlay = OrbOverlay(style="mascot")

    overlay.set_style("voice_orb")

    assert overlay._style == "voice_orb"


def test_unknown_style_recovers_to_the_mascot_instead_of_raising() -> None:
    # A jarvis.toml written by a NEWER build must not leave an older one with no
    # overlay at all — and set_style() is called from a REST route, where an
    # exception would surface as a 500 on a cosmetic setting.
    assert _coerce_style("nonsense") == "mascot"
    assert _coerce_style(None) == "mascot"
    assert OrbOverlay(style="nonsense")._style == "mascot"
    OrbOverlay(style="mascot").set_style("nonsense")  # no raise


def test_every_orb_style_maps_to_a_renderer() -> None:
    overlay = OrbOverlay(style="mascot")
    for style in ORB_STYLES:
        assert overlay._build_renderer(style) is not None, style


class _FakeRoot:
    """Minimal Tk stand-in: refuses the colour key like a bare X11 session."""

    def __init__(self, *, supported: bool) -> None:
        self.supported = supported
        self.background: str | None = None

    def wm_attributes(self, name: str, value: str) -> None:
        if not self.supported:
            raise tk.TclError(f"bad attribute {name}")

    def configure(self, **kwargs: object) -> None:
        self.background = str(kwargs.get("bg"))


def test_color_key_applied_when_the_session_supports_it() -> None:
    root = _FakeRoot(supported=True)

    assert _apply_color_key(root) is True
    assert root.background == overlay_mod.COLOR_KEY_HEX


def test_color_key_refusal_is_reported_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    root = _FakeRoot(supported=False)

    with caplog.at_level("WARNING", logger="jarvis.orb"):
        supported = _apply_color_key(root)

    assert supported is False
    # AP-30: the window will not appear, so the reason has to be visible.
    assert any("colour key" in record.message for record in caplog.records)
