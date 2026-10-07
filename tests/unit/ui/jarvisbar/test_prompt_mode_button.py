"""The jarvis-bar's idle LEFT control: pause Prompt Mode, and bring it back.

Two levels, because the maintainer asked for two (2026-08-28). The SETTING
lives in jarvis.toml and belongs to the settings card and the front-page
pill; with it off the bar draws nothing at all. The PAUSE is the bar's own,
runtime-only: the sparkle is there while the setting is on, a click holds the
rewriting only until the same button is pressed again, and the mark takes a
red slash — the muted mic's language — so that second click is obvious. The
setting is never written from here, which is the whole point: the first
version turned it off for good, and then there was no way back.

A click fires the wired ``_on_prompt_mode_toggle`` (the OrbBusBridge points
it at ``DictationPromptModePauseToggleRequested``) and optimistically flips
the local mirror; the authoritative ``DictationPromptModeChanged`` carries
both levels and is reconciled via ``set_prompt_mode``, exactly like mute.

Every surface carries the same two methods (the Tk bar, the Qt bar, the IPC
proxy, the null surface) and the host protocol carries the op and the event —
pinned here so no renderer can silently lack the control.
"""

from __future__ import annotations

from jarvis.ui.jarvisbar import host
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
    return next(
        (a + b) / 2
        for action, a, b in R.controls.pet_strip_layout(R.strip_scale()).slots
        if action == "orb"
    )


def _patch_pipeline(monkeypatch, fake) -> None:
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: fake)


# --------------------------------------------------------------------------- #
# The click zone
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# The Tk surface
# --------------------------------------------------------------------------- #


def test_the_click_pauses_and_a_second_click_brings_it_back(monkeypatch) -> None:
    """The headline. The first version switched the setting off, the sparkle
    vanished with it, and the mode could not be brought back from the bar at
    all — which is exactly what the maintainer reported."""
    bar = JarvisBarOverlay()
    bar.set_prompt_mode(True)  # the setting; the sparkle exists only then
    fired: list[int] = []
    bar.set_on_prompt_mode_toggle(lambda: fired.append(1))
    fake = _FakePipeline()
    _patch_pipeline(monkeypatch, fake)

    bar._on_click(_sparkle_x(), hovered=True)
    assert fired == [1]
    assert bar._prompt_mode_paused is True  # struck through on the next frame
    assert bar._prompt_mode is True, "the SETTING must survive a pause"
    assert fake.session_calls == 0  # the control is not a session start

    bar._on_click(_sparkle_x(), hovered=True)
    assert fired == [1, 1]
    assert bar._prompt_mode_paused is False  # ...and back, in one click
    assert bar._prompt_mode is True


def test_no_callback_means_no_false_pause(monkeypatch) -> None:
    """A boot-race click before the bridge wired the toggle must not strike the
    sparkle through with nothing behind it (mirrors the mute button's rule)."""
    bar = JarvisBarOverlay()
    bar.set_prompt_mode(True)
    _patch_pipeline(monkeypatch, _FakePipeline())
    bar._on_click(_sparkle_x(), hovered=True)
    assert bar._prompt_mode_paused is False


def test_set_prompt_mode_mirrors_both_levels() -> None:
    bar = JarvisBarOverlay()
    bar.set_prompt_mode(True)
    assert (bar._prompt_mode, bar._prompt_mode_paused) == (True, False)
    bar.set_prompt_mode(True, True)
    assert (bar._prompt_mode, bar._prompt_mode_paused) == (True, True)
    bar.set_prompt_mode(0)
    assert (bar._prompt_mode, bar._prompt_mode_paused) == (False, False)


# --------------------------------------------------------------------------- #
# The renderer
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# The host protocol and the other surfaces
# --------------------------------------------------------------------------- #


class _RecordingSurface:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_prompt_mode(self, enabled: bool, paused: bool = False) -> None:
        self.calls.append(("set_prompt_mode", enabled, paused))


def test_host_dispatches_the_set_prompt_mode_op() -> None:
    surface = _RecordingSurface()
    msg = {"op": "set_prompt_mode", "enabled": True, "paused": True}
    assert host.dispatch(surface, msg) is True
    assert ("set_prompt_mode", True, True) in surface.calls


def test_the_null_surface_accepts_both_methods() -> None:
    null = NullOverlay()
    null.set_prompt_mode(True)
    null.set_on_prompt_mode_toggle(lambda: None)
