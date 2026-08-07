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


def test_only_modes_with_a_live_feed_react_to_the_level() -> None:
    assert VoiceOrbRenderer._input_level("listen", 0.7) == pytest.approx(0.7)
    # Speaking rides the REAL TTS loudness the bridge forwards, so the sphere
    # swells on Jarvis's actual voice instead of a synthetic cadence.
    assert VoiceOrbRenderer._input_level("speak", 0.7) == pytest.approx(0.7)
    # Recording keeps a floor so a quiet moment never reads as a dead orb.
    assert VoiceOrbRenderer._input_level("dictate", 0.0) > 0.0
    # Transcribing has no live feed; a stale sample must not animate the orb.
    assert VoiceOrbRenderer._input_level("dictate_transcribing", 0.9) == 0.0
    assert VoiceOrbRenderer._input_level("think", 0.9) == 0.0
    assert VoiceOrbRenderer._input_level("listen", None) == 0.0


def _lit_radius(frame: np.ndarray) -> float:
    """Half the width of the SPHERE — measured as the longest unbroken run of
    painted pixels across the middle row.

    Not "leftmost to rightmost painted pixel": the aura is painted too, and it
    is deliberately full of gaps (its falloff is density, not alpha), so an
    extent measure would report the corona instead of the sphere.
    """
    lit = np.any(frame != np.asarray(KEY, dtype=np.int16), axis=-1)
    row = lit[frame.shape[0] // 2]
    best = run = 0
    for painted in row:
        run = run + 1 if painted else 0
        best = max(best, run)
    return best / 2.0


def test_a_loud_voice_visibly_swells_the_sphere() -> None:
    """The whole point of dropping the speech bubble: the orb IS the indicator.

    A swell that only exists in the arithmetic — because the resting sphere
    already fills its window and the growth clips away — is the bug this pins.
    """
    quiet = VoiceOrbRenderer(size=108, color_key=KEY)
    loud = VoiceOrbRenderer(size=108, color_key=KEY)
    for step in range(40):
        t = step / 30.0
        quiet_frame = _frame(quiet, t, "speak", 0.0)
        loud_frame = _frame(loud, t, "speak", 1.0)
    assert _lit_radius(loud_frame) - _lit_radius(quiet_frame) >= 4.0


def test_the_resting_sphere_leaves_room_to_grow() -> None:
    idle = VoiceOrbRenderer(size=108, color_key=KEY)
    for step in range(30):
        frame = _frame(idle, step / 30.0, "idle", None)
    assert _lit_radius(frame) < 54.0  # strictly inside the window


def _outside_sphere(frame: np.ndarray, renderer: VoiceOrbRenderer) -> np.ndarray:
    """Mask of pixels beyond the sphere but inside the window."""
    radius = renderer._pixel_radius
    sphere_edge = _lit_radius(frame) / (frame.shape[0] / 2.0)
    return (radius > sphere_edge + 0.04) & (radius < 0.95)


def test_a_resting_orb_throws_off_no_aura() -> None:
    """Idle has to stay exactly as calm as it was — no glow, no cost."""
    renderer = VoiceOrbRenderer(size=108, color_key=KEY)
    for step in range(30):
        frame = _frame(renderer, step / 30.0, "idle", None)
    outside = _outside_sphere(frame, renderer)
    painted = np.any(frame != np.asarray(KEY, dtype=np.int16), axis=-1)
    assert not (painted & outside).any()


def test_a_loud_voice_lights_an_aura_around_the_sphere() -> None:
    renderer = VoiceOrbRenderer(size=108, color_key=KEY)
    for step in range(30):
        frame = _frame(renderer, step / 30.0, "speak", 1.0)
    outside = _outside_sphere(frame, renderer)
    painted = np.any(frame != np.asarray(KEY, dtype=np.int16), axis=-1)
    assert (painted & outside).sum() > 40


def test_the_aura_is_density_not_a_solid_band() -> None:
    """Its falloff has to come from GAPS: a colour-keyed window has no alpha,
    and a solid ring read as machined brass rather than as energy."""
    renderer = VoiceOrbRenderer(size=108, color_key=KEY)
    for step in range(30):
        frame = _frame(renderer, step / 30.0, "speak", 1.0)
    outside = _outside_sphere(frame, renderer)
    painted = np.any(frame != np.asarray(KEY, dtype=np.int16), axis=-1)
    lit = (painted & outside).sum()
    dark = (~painted & outside).sum()
    assert lit > 0 and dark > 0, "the corona is either absent or completely solid"


def test_the_aura_never_reaches_the_window_corners() -> None:
    """A pixel in the corner would put a stray ember at the window edge, and
    the surface's transparency contract expects the corners keyed out."""
    renderer = VoiceOrbRenderer(size=108, color_key=KEY)
    for step in range(30):
        frame = _frame(renderer, step / 30.0, "speak", 1.0)
    for y, x in ((0, 0), (0, 107), (107, 0), (107, 107)):
        assert tuple(frame[y, x]) == KEY


def test_thinking_churns_the_palette_harder_than_resting() -> None:
    """Thinking has nothing to hear, so it says so with colour, not with text."""
    assert MOTIONS["think"].color_churn > MOTIONS["idle"].color_churn * 2

    def banding(mode: str) -> float:
        """How tightly colour still tracks HEIGHT (1 = perfect ramp, 0 = mixed).

        This is the honest measure of "the colours mix": a resting orb is a
        vertical ivory→amber ramp, so its colour is almost a function of y.
        Churning breaks that link — the cloud decides the colour, not the row.
        """
        renderer = VoiceOrbRenderer(size=108, color_key=KEY)
        for step in range(45):
            frame = _frame(renderer, step / 30.0, mode, None)
        lit = np.any(frame != np.asarray(KEY, dtype=np.int16), axis=-1)
        rows = np.repeat(np.arange(frame.shape[0])[:, None], frame.shape[1], axis=1)
        return abs(float(np.corrcoef(rows[lit], frame[..., 2][lit])[0, 1]))

    assert banding("think") < banding("idle")


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
