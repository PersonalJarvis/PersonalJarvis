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
    return round(R.WIN_W / 2.0 + 0.30 * R.ACTIVE_W)


def _patch_pipeline(monkeypatch, fake) -> None:
    monkeypatch.setattr(
        "jarvis.core.runtime_refs.get_speech_pipeline", lambda: fake
    )


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


def test_muted_idle_pill_stays_open_not_collapsed():
    """A muted user must not be left with the tiny empty collapsed pill: the bar
    stays OPEN so the red rim + slashed mic (the ONLY unmute target — voice can't
    unmute while Jarvis is deaf) are always on screen. Forensic 2026-06-29: a
    user got 'stuck muted' and read the silent collapsed bar as 'frozen'."""
    assert R.target_pill_size("idle", hovered=False, muted=True) == (R.OPEN_W, R.OPEN_H)
    assert R.target_pill_size("idle", hovered=False, muted=False) == (
        R.COLLAPSED_W,
        R.COLLAPSED_H,
    )


def test_muted_idle_shows_slashed_mic_without_hover():
    """Muted standby renders the slashed mic even with no hover, so the muted
    state (and the click-to-unmute target) is always visible — unlike the clean
    empty idle pill when unmuted."""
    empty = R.JarvisBarRenderer().render(0.1, "idle", 0.0, hovered=False, muted=False)
    muted = R.JarvisBarRenderer().render(0.1, "idle", 0.0, hovered=False, muted=True)
    assert list(empty.getdata()) != list(muted.getdata())
