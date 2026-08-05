"""The desktop voice orb renders a colour-keyed sphere that reacts to modes.

Pixel-exactness against the in-app canvas is not something a test can assert
(different rasterizers), so these pin the properties the overlay depends on: a
binary silhouette on the exact key colour, a palette that stays in the product's
ivory/gold range, and per-mode motion that actually differs.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.ui.jarvisbar.modes import MODES
from ui.orb.voice_orb import MOTIONS, VoiceOrbRenderer

KEY = (255, 0, 255)


def _frame(renderer: VoiceOrbRenderer, t: float, mode: str, level=None) -> np.ndarray:
    return np.asarray(renderer.render(t, mode, level), dtype=np.int16)


def test_every_surface_mode_has_motion() -> None:
    # A mode the surface accepts but the renderer does not know would silently
    # freeze the orb on its previous look (AP-4 / BUG-008 shape).
    assert set(MOTIONS) == set(MODES)


def test_frame_is_key_coloured_outside_a_solid_circle() -> None:
    renderer = VoiceOrbRenderer(size=108, color_key=KEY)
    frame = _frame(renderer, 0.0, "listen")

    assert frame.shape == (108, 108, 3)
    # Corners are outside any inscribed circle → must be the exact key colour,
    # or Windows leaves an opaque square on the desktop.
    for y, x in ((0, 0), (0, 107), (107, 0), (107, 107)):
        assert tuple(frame[y, x]) == KEY
    # The centre is orb, not key.
    assert tuple(frame[54, 54]) != KEY


def test_no_blended_edge_pixels_survive_the_colour_key() -> None:
    """Every pixel is either orb or exactly the key colour — no pink fringe."""
    renderer = VoiceOrbRenderer(size=108, color_key=KEY)
    frame = _frame(renderer, 0.0, "speak")

    r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
    # A blend of the gold palette with magenta shows up as a high-red,
    # low-green, high-blue pixel that is NOT the key itself.
    keyed = (r == 255) & (g == 0) & (b == 255)
    magenta_ish = (r > 180) & (g < 90) & (b > 140) & ~keyed
    assert not magenta_ish.any()


def test_palette_stays_in_the_product_range() -> None:
    renderer = VoiceOrbRenderer(size=108, color_key=KEY)
    frame = _frame(renderer, 0.0, "idle")
    keyed = (frame[..., 0] == 255) & (frame[..., 1] == 0) & (frame[..., 2] == 255)
    orb = frame[~keyed]

    assert orb.size > 0
    # Warm: red >= green >= blue holds across the whole ivory→amber ramp.
    assert (orb[:, 0] >= orb[:, 1]).mean() > 0.98
    assert (orb[:, 1] >= orb[:, 2]).mean() > 0.98


def test_modes_look_different() -> None:
    listening = VoiceOrbRenderer(size=108, color_key=KEY)
    thinking = VoiceOrbRenderer(size=108, color_key=KEY)
    # Same clock, different mode: the fields diverge because pace, turbulence
    # and energy differ, not because time moved on.
    for t in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        left = _frame(listening, t, "listen")
        right = _frame(thinking, t, "think")
    assert np.abs(left - right).mean() > 1.0


def test_only_listening_modes_react_to_the_live_level() -> None:
    assert VoiceOrbRenderer._input_level("listen", 0.7) == pytest.approx(0.7)
    # Recording keeps a floor so a quiet moment never reads as a dead orb.
    assert VoiceOrbRenderer._input_level("dictate", 0.0) > 0.0
    # Transcribing has no live feed; a stale sample must not animate the orb.
    assert VoiceOrbRenderer._input_level("dictate_transcribing", 0.9) == 0.0
    assert VoiceOrbRenderer._input_level("think", 0.9) == 0.0
    assert VoiceOrbRenderer._input_level("listen", None) == 0.0


def test_field_is_recomputed_at_a_capped_rate() -> None:
    """The overlay paints at ~60 fps; the procedural field must not follow."""
    renderer = VoiceOrbRenderer(size=108, color_key=KEY)
    calls = {"n": 0}
    original = renderer._paint_weather

    def counted(impact):
        calls["n"] += 1
        return original(impact)

    renderer._paint_weather = counted  # type: ignore[method-assign]
    for step in range(60):  # one second of 60 fps frames
        renderer.render(step / 60.0, "listen", 0.4)
    assert calls["n"] <= 22  # 20 fps + the initial frame, with slack


def test_mouth_ops_are_accepted_and_do_nothing() -> None:
    # The shared surface drives these for the mascot; the orb must answer them
    # rather than make the surface special-case renderers.
    renderer = VoiceOrbRenderer(size=48, color_key=KEY)
    renderer.start_mouth_anim(1.0, 0.0)
    renderer.stop_mouth_anim()
    assert renderer.render(0.0, "speak", None).size == (48, 48)
