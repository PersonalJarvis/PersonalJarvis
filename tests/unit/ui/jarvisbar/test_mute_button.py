"""The jarvis-bar RIGHT control is the microphone mute toggle.

Replaces the former endpoint-free-dictation square (maintainer request,
2026-06-28): clicking the mic fires the wired ``_on_mute_toggle`` (which the
OrbBusBridge points at ``VoiceMuteToggleRequested``) and optimistically flips
the local mirror so the slashed-mic icon shows on the very next frame. The
authoritative ``VoiceMuteChanged`` is reconciled via ``set_muted``.
"""

from __future__ import annotations

from jarvis.ui.jarvisbar import renderer as R
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay
from ui.orb.controls import pet_strip_layout


class _FakePipeline:
    """Minimal pipeline: the mute path only needs a non-None pipeline + the
    session probe; the toggle itself goes through ``_on_mute_toggle``."""

    def is_session_active(self) -> bool:
        return True

    def request_voice_session(self) -> None:  # pragma: no cover - unused here
        ...

    def request_hangup(self) -> None:  # pragma: no cover - unused here
        ...


def _mic_x() -> int:
    """The on-screen microphone-button centre (mirror renderer x_right)."""
    slot = next(s for s in pet_strip_layout(R.strip_scale()).slots if s[0] == "mic_mute")
    return round((slot[1] + slot[2]) / 2)


def _patch_pipeline(monkeypatch, fake) -> None:
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: fake)


def test_mic_click_fires_toggle_and_optimistically_mutes(monkeypatch):
    bar = JarvisBarOverlay()
    fired: list[int] = []
    bar.set_on_mute_toggle(lambda: fired.append(1))
    _patch_pipeline(monkeypatch, _FakePipeline())

    assert bar._muted is False
    bar._on_click(_mic_x(), hovered=True)
    assert fired == [1]
    assert bar._muted is True  # optimistic flip → slash shows immediately

    bar._on_click(_mic_x(), hovered=True)
    assert fired == [1, 1]
    assert bar._muted is False  # toggles back


def test_mic_callback_does_not_require_a_child_speech_pipeline(monkeypatch):
    """The macOS host has no pipeline, but its mute IPC callback must fire."""
    bar = JarvisBarOverlay()
    fired: list[int] = []
    bar.set_on_mute_toggle(lambda: fired.append(1))
    _patch_pipeline(monkeypatch, None)

    bar._on_click(_mic_x(), hovered=True)

    assert fired == [1]
    assert bar._muted is True


def test_mic_click_without_callback_is_noop(monkeypatch):
    bar = JarvisBarOverlay()
    bar._on_mute_toggle = None  # boot race: bridge not wired yet
    _patch_pipeline(monkeypatch, _FakePipeline())

    bar._on_click(_mic_x(), hovered=True)
    assert bar._muted is False  # no callback → genuine no-op, no false slash


def test_set_muted_mirrors_authoritative_state():
    bar = JarvisBarOverlay()
    bar.set_muted(True)
    assert bar._muted is True
    bar.set_muted(False)
    assert bar._muted is False


def test_muted_render_differs_from_unmuted():
    """The slashed-mic (red disc + white slash) must be visibly distinct from
    the live mic so the user can tell at a glance whether they are muted."""
    rnd = R.JarvisBarRenderer()
    for _ in range(60):  # settle the eased pill size
        rnd.render(0.0, "listen", 0.0, hovered=True, muted=False)
    unmuted = list(rnd.render(0.1, "listen", 0.0, hovered=True, muted=False).getdata())
    muted = list(rnd.render(0.1, "listen", 0.0, hovered=True, muted=True).getdata())
    assert unmuted != muted


def test_muted_render_is_safe_for_every_mode():
    rnd = R.JarvisBarRenderer()
    for mode in ("idle", "listen", "speak", "think"):
        img = rnd.render(0.1, mode, 0.5, hovered=True, muted=True)
        assert img.size == (R.WIN_W, R.WIN_H)


def test_muted_idle_keeps_all_controls_available():
    assert R.target_pill_size("idle", muted=True) == (R.WIN_W, R.WIN_H)
    assert R.target_pill_size("idle", muted=False) == (R.WIN_W, R.WIN_H)


def test_muted_idle_shows_slashed_mic_without_hover():
    """Muted standby renders the slashed mic even with no hover, so the muted
    state (and the click-to-unmute target) is always visible — unlike the clean
    empty idle pill when unmuted."""
    empty = R.JarvisBarRenderer().render(0.1, "idle", 0.0, hovered=False, muted=False)
    muted = R.JarvisBarRenderer().render(0.1, "idle", 0.0, hovered=False, muted=True)
    assert list(empty.getdata()) != list(muted.getdata())
