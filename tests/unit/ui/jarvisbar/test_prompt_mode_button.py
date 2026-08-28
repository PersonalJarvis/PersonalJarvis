"""The jarvis-bar's idle LEFT control is the Prompt Mode switch.

Maintainer request 2026-08-28: "when Prompt Mode is on, the bar should offer
a way to switch it off when I hover over it." The sparkle takes the close-X's
slot on the resting pill — lit in the accent while every dictation becomes a
prompt, visible at a glance without a hover (the pill opens like it does for
mute), dim while off and only drawn while the controls are up. A click on it
fires the wired ``_on_prompt_mode_toggle`` (the OrbBusBridge points it at
``DictationPromptModeToggleRequested``) and optimistically flips the local
mirror; the authoritative ``DictationPromptModeChanged`` is reconciled via
``set_prompt_mode``, exactly like the mute button.

Every surface carries the same two methods (the Tk bar, the Qt bar, the IPC
proxy, the null surface) and the host protocol carries the op and the event —
pinned here so no renderer can silently lack the control.
"""

from __future__ import annotations

from jarvis.ui.jarvisbar import host, interaction
from jarvis.ui.jarvisbar import renderer as R
from jarvis.ui.jarvisbar.null_overlay import NullOverlay
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


class _FakePipeline:
    def __init__(self) -> None:
        self.session_calls = 0

    def is_session_active(self) -> bool:
        return False

    def request_voice_session(self) -> None:
        self.session_calls += 1

    def request_hangup(self) -> None:  # pragma: no cover - unused here
        ...


def _sparkle_x() -> float:
    """The on-screen sparkle centre on the OPEN (idle) pill."""
    return R.WIN_W / 2.0 - R.SPARKLE_CENTRE_FRAC * R.OPEN_W


def _patch_pipeline(monkeypatch, fake) -> None:
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: fake)


# --------------------------------------------------------------------------- #
# The click zone
# --------------------------------------------------------------------------- #


def test_the_hit_box_sits_where_the_renderer_draws_the_sparkle() -> None:
    """The two live in different modules on purpose — ``interaction`` stays
    dependency-free and cannot import the renderer — so the shared number is
    pinned here. A drawn glyph must be a working glyph."""
    assert interaction._SPARKLE_CENTRE_FRAC == R.SPARKLE_CENTRE_FRAC


def test_the_sparkle_fits_inside_the_pill() -> None:
    """The defect this replaced: at the close-X's 0.42 the star's left tip
    reached past the rim and drew onto the colour key — a star floating
    OUTSIDE the pill, which is why the maintainer could not read it."""
    for pw, ph in ((R.OPEN_W, R.OPEN_H), (R.ACTIVE_W, R.ACTIVE_H)):
        left_rim = R.WIN_W / 2.0 - pw / 2.0
        centre = R.WIN_W / 2.0 - R.SPARKLE_CENTRE_FRAC * pw
        radius = R.SPARKLE_R_FRAC * ph
        # The main star sits at ``centre - 0.15*r`` and reaches ``r`` further left.
        assert centre - 1.15 * radius > left_rim
        # The companion reaches 1.27*r above centre; the pill is ph/2 tall.
        assert 1.27 * radius < ph / 2.0


def test_a_click_on_the_sparkle_switches_prompt_mode_off() -> None:
    for hovered in (True, False):
        action = interaction.resolve_click(
            _sparkle_x(), R.WIN_W, "idle", hovered=hovered, pill_w=R.OPEN_W, prompt_mode=True
        )
        assert action == "prompt_mode_toggle", hovered


def test_with_prompt_mode_off_that_spot_is_ordinary_bar() -> None:
    """Nothing is drawn there, so nothing is clicked there — and the bar can
    only ever switch the mode OFF. Turning it on stays with the settings card
    and the front-page pill (maintainer, 2026-08-28)."""
    for hovered in (True, False):
        action = interaction.resolve_click(
            _sparkle_x(), R.WIN_W, "idle", hovered=hovered, pill_w=R.OPEN_W, prompt_mode=False
        )
        assert action == "talk", hovered


def test_the_idle_body_still_talks_and_the_mic_still_mutes() -> None:
    for prompt_mode in (False, True):
        assert (
            interaction.resolve_click(
                R.WIN_W / 2, R.WIN_W, "idle", hovered=True, prompt_mode=prompt_mode
            )
            == "talk"
        )
        assert (
            interaction.resolve_click(
                R.WIN_W * 0.9, R.WIN_W, "idle", hovered=True, prompt_mode=prompt_mode
            )
            == "mute"
        )


def test_a_live_bar_keeps_the_close_x_untouched() -> None:
    """During a session the left control is the hang-up X, whatever the switch
    is doing: a user reaching for the X must not silently change how their next
    dictation is written."""
    x = R.WIN_W / 2.0 - 0.42 * R.ACTIVE_W
    for mode in ("listen", "speak", "think"):
        assert (
            interaction.resolve_click(
                x, R.WIN_W, mode, hovered=True, pill_w=R.ACTIVE_W, prompt_mode=True
            )
            == "hangup"
        )
    for mode in ("dictate", "dictate_transcribing"):
        assert (
            interaction.resolve_click(
                x, R.WIN_W, mode, hovered=True, pill_w=R.ACTIVE_W, prompt_mode=True
            )
            == "dictation_stop"
        )


# --------------------------------------------------------------------------- #
# The Tk surface
# --------------------------------------------------------------------------- #


def test_sparkle_click_fires_toggle_and_optimistically_clears(monkeypatch) -> None:
    bar = JarvisBarOverlay()
    bar.set_prompt_mode(True)  # the only state in which the sparkle exists
    fired: list[int] = []
    bar.set_on_prompt_mode_toggle(lambda: fired.append(1))
    fake = _FakePipeline()
    _patch_pipeline(monkeypatch, fake)

    bar._on_click(_sparkle_x(), hovered=True)
    assert fired == [1]
    assert bar._prompt_mode is False  # optimistic flip → the sparkle goes
    assert fake.session_calls == 0  # the switch is not a session start

    # ...and with the mode off the same spot is ordinary bar again.
    bar._on_click(_sparkle_x(), hovered=True)
    assert fired == [1]
    assert fake.session_calls == 1


def test_no_callback_means_no_false_flip(monkeypatch) -> None:
    """A boot-race click before the bridge wired the toggle must not clear the
    sparkle with nothing behind it (mirrors the mute button's rule)."""
    bar = JarvisBarOverlay()
    bar.set_prompt_mode(True)
    _patch_pipeline(monkeypatch, _FakePipeline())
    bar._on_click(_sparkle_x(), hovered=True)
    assert bar._prompt_mode is True


def test_set_prompt_mode_mirrors_the_authoritative_value() -> None:
    bar = JarvisBarOverlay()
    bar.set_prompt_mode(True)
    assert bar._prompt_mode is True
    bar.set_prompt_mode(0)
    assert bar._prompt_mode is False


# --------------------------------------------------------------------------- #
# The renderer
# --------------------------------------------------------------------------- #


def test_prompt_mode_opens_the_resting_pill_like_mute_does() -> None:
    assert R.target_pill_size("idle", False) == (R.COLLAPSED_W, R.COLLAPSED_H)
    assert R.target_pill_size("idle", False, prompt_mode=True) == (R.OPEN_W, R.OPEN_H)
    # A live session is 2x regardless.
    assert R.target_pill_size("listen", False, prompt_mode=True) == (R.ACTIVE_W, R.ACTIVE_H)


def _settled(**kw):
    """One idle frame, rendered until the eased pill size has converged."""
    rend = R.JarvisBarRenderer()
    img = None
    for i in range(40):
        img = rend.render(i * 0.016, "idle", 0.0, **kw)
    assert img is not None
    return img


def _differing(one, other) -> list[tuple[tuple, tuple]]:
    """The pixels the two frames disagree about, paired ``(one, other)``.

    Between two frames that differ ONLY in the switch's state, these pixels
    ARE the sparkle — which is why the comparison is made this way rather
    than by hunting a region: the pill rim and the colour-keyed corners are
    identical in both frames and drop out on their own.
    """
    return [
        (p, q)
        for p, q in zip(one.get_flattened_data(), other.get_flattened_data(), strict=True)
        if p != q
    ]


def _warmth(pixels) -> float:
    """Mean red-minus-blue over *pixels* — how much accent is in the ink.

    Brightness alone cannot tell the two states apart (an antialiased star
    blends into the near-neutral pill fill either way), but the hue can: the
    accent (231, 196, 110) is 121 apart on this axis, the standby grey
    (150, 140, 120) only 30, and the fill it blends into is 2.
    """
    return sum(p[0] - p[2] for p in pixels) / max(1, len(pixels))


def test_the_sparkle_is_drawn_in_the_accent_and_only_when_the_mode_is_on() -> None:
    """The switch reports a state and offers the way out of it. With the mode
    off it is not dimmed — it is not there at all (maintainer, 2026-08-28),
    which is also why the hovered bar with the mode off must look exactly like
    it did before this feature existed."""
    changed = _differing(_settled(hovered=True), _settled(hovered=True, prompt_mode=True))
    assert changed, "switching the mode on must change what the bar draws"

    off_warmth = _warmth([p for p, _ in changed])
    on_warmth = _warmth([q for _, q in changed])
    assert on_warmth > 2 * off_warmth, (
        f"the sparkle must read as the accent against the bare pill "
        f"(warmth off={off_warmth:.0f}, on={on_warmth:.0f})"
    )


def test_prompt_mode_is_visible_on_the_resting_bar() -> None:
    """No hover needed: "every dictation comes out rewritten" is a state the
    user must never be surprised by, so the resting pill opens and shows the
    lit sparkle — while a bar with nothing on stays the clean empty pill."""
    resting_off = _settled()
    resting_on = _settled(prompt_mode=True)
    assert _differing(resting_off, resting_on), "Prompt Mode on must be visible at rest"

    # The pill with nothing on draws no ink at all (the historical contract:
    # "when nothing is happening, nothing is in the bar").
    arr = R.key_to_alpha(resting_off)
    ink = [
        (r, g, b)
        for r, g, b, a in arr.get_flattened_data()
        if a and (r, g, b) not in (R.PILL_BG, R.PILL_BORDER)
    ]
    assert _warmth(ink) < _warmth([(231, 196, 110)])


def test_no_part_of_the_sparkle_is_drawn_outside_the_pill() -> None:
    """The defect the maintainer photographed: the mark's tips crossed the rim
    and drew onto the colour key, so the bar wore a magenta fleck and a star
    that looked like a stray artefact instead of a control. Every pixel the
    switch adds must land inside the pill — with room to spare, because the 4x
    LANCZOS downscale rings a pixel or two past the shape it drew."""
    off = R.key_to_alpha(_settled())
    on = R.key_to_alpha(_settled(prompt_mode=True))
    ph = float(R.OPEN_H)
    cy = R.pill_center_y(ph)
    top, bottom = cy - ph / 2.0, cy + ph / 2.0
    left = R.WIN_W / 2.0 - R.OPEN_W / 2.0

    width = off.size[0]
    escaped = []
    for i, (before, after) in enumerate(
        zip(off.get_flattened_data(), on.get_flattened_data(), strict=True)
    ):
        if before == after:
            continue
        y, x = divmod(i, width)
        if not (top <= y <= bottom and x >= left):
            escaped.append((x, y, after))
    assert escaped == [], f"the sparkle drew outside the pill: {escaped[:5]}"


# --------------------------------------------------------------------------- #
# The host protocol and the other surfaces
# --------------------------------------------------------------------------- #


class _RecordingSurface:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_prompt_mode(self, enabled: bool) -> None:
        self.calls.append(("set_prompt_mode", enabled))


def test_host_dispatches_the_set_prompt_mode_op() -> None:
    surface = _RecordingSurface()
    assert host.dispatch(surface, {"op": "set_prompt_mode", "enabled": True}) is True
    assert ("set_prompt_mode", True) in surface.calls


def test_the_null_surface_accepts_both_methods() -> None:
    null = NullOverlay()
    null.set_prompt_mode(True)
    null.set_on_prompt_mode_toggle(lambda: None)
