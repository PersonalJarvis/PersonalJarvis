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
    """The on-screen sparkle centre on the OPEN (hovered idle) pill — the
    renderer's ``cx - 0.42 * pw``."""
    return R.WIN_W / 2.0 - 0.42 * R.OPEN_W


def _patch_pipeline(monkeypatch, fake) -> None:
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: fake)


# --------------------------------------------------------------------------- #
# The click zone
# --------------------------------------------------------------------------- #


def test_idle_hovered_sparkle_click_flips_prompt_mode() -> None:
    action = interaction.resolve_click(
        _sparkle_x(), R.WIN_W, "idle", hovered=True, pill_w=R.OPEN_W
    )
    assert action == "prompt_mode_toggle"


def test_the_sparkle_is_only_a_control_while_the_controls_are_up() -> None:
    """Not hovered, nothing is drawn there, so nothing is clicked there: the
    resting pill's body starts a session as it always did."""
    action = interaction.resolve_click(
        _sparkle_x(), R.WIN_W, "idle", hovered=False, pill_w=R.OPEN_W
    )
    assert action == "talk"


def test_the_idle_body_still_talks_and_the_mic_still_mutes() -> None:
    assert interaction.resolve_click(R.WIN_W / 2, R.WIN_W, "idle", hovered=True) == "talk"
    assert interaction.resolve_click(R.WIN_W * 0.9, R.WIN_W, "idle", hovered=True) == "mute"


def test_a_live_bar_keeps_the_close_x_in_that_slot() -> None:
    """During a session the left control is the hang-up X, never the switch:
    a user reaching for the X must not silently change how their next
    dictation is written."""
    x = R.WIN_W / 2.0 - 0.42 * R.ACTIVE_W
    for mode in ("listen", "speak", "think"):
        assert (
            interaction.resolve_click(x, R.WIN_W, mode, hovered=True, pill_w=R.ACTIVE_W)
            == "hangup"
        )
    for mode in ("dictate", "dictate_transcribing"):
        assert (
            interaction.resolve_click(x, R.WIN_W, mode, hovered=True, pill_w=R.ACTIVE_W)
            == "dictation_stop"
        )


# --------------------------------------------------------------------------- #
# The Tk surface
# --------------------------------------------------------------------------- #


def test_sparkle_click_fires_toggle_and_optimistically_flips(monkeypatch) -> None:
    bar = JarvisBarOverlay()
    fired: list[int] = []
    bar.set_on_prompt_mode_toggle(lambda: fired.append(1))
    fake = _FakePipeline()
    _patch_pipeline(monkeypatch, fake)

    assert bar._prompt_mode is False
    bar._on_click(_sparkle_x(), hovered=True)
    assert fired == [1]
    assert bar._prompt_mode is True  # optimistic flip → lit sparkle next frame
    assert fake.session_calls == 0  # the switch is not a session start

    bar._on_click(_sparkle_x(), hovered=True)
    assert fired == [1, 1]
    assert bar._prompt_mode is False


def test_no_callback_means_no_false_sparkle(monkeypatch) -> None:
    """A boot-race click before the bridge wired the toggle must not light a
    sparkle with nothing behind it (mirrors the mute button's rule)."""
    bar = JarvisBarOverlay()
    _patch_pipeline(monkeypatch, _FakePipeline())
    bar._on_click(_sparkle_x(), hovered=True)
    assert bar._prompt_mode is False


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


def test_the_sparkle_says_on_in_the_accent_and_off_in_grey() -> None:
    """The switch's whole job is to be readable at a glance: lit in the accent
    while every dictation becomes a prompt, grey while it does not."""
    changed = _differing(_settled(hovered=True), _settled(hovered=True, prompt_mode=True))
    assert changed, "flipping the switch must change what the bar draws"

    off_warmth = _warmth([p for p, _ in changed])
    on_warmth = _warmth([q for _, q in changed])
    assert on_warmth > 2 * off_warmth, (
        f"the lit sparkle must read as the accent, not the standby grey "
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
