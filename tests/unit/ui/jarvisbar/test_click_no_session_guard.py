"""Regression — the bar must never fire a destructive (and useless) hang-up
when no voice session is actually live.

Forensic 2026-06-28 (data/jarvis_desktop.log ~07:49): a wake popped the bar into
its active "listen" look, but the post-hangup wake-lock cooldown rejected the
session, so NO ``VoiceSessionStarted`` and NO ``IDLE`` ``SystemStateChanged``
followed — the bridge left the bar stuck in the active mode. In that stuck state
``resolve_click`` reads a close-X click as ``"hangup"`` → ``request_hangup()``, a
no-op that just traps the user ("frozen, nothing works"). The bar must instead
treat a click as a session start whenever no session is live, so the user can
always escape the stuck state.
"""
from __future__ import annotations

from jarvis.ui.jarvisbar import renderer as R
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


class _FakePipeline:
    def __init__(self, *, session_active: bool) -> None:
        self._session_active = session_active
        self.hangup_calls = 0
        self.session_calls = 0
        self.ptt_calls = 0

    def is_session_active(self) -> bool:
        return self._session_active

    def request_hangup(self) -> None:
        self.hangup_calls += 1

    def request_voice_session(self) -> None:
        self.session_calls += 1

    def request_ptt_toggle(self) -> None:
        self.ptt_calls += 1


def _x_glyph() -> int:
    """The on-screen End-X centre for the deployed pill (mirror renderer)."""
    return round(R.WIN_W / 2.0 - 0.30 * R.ACTIVE_W)


def _patch_pipeline(monkeypatch, fake) -> None:
    # _on_click does `from jarvis.core.runtime_refs import get_speech_pipeline`
    # at call time, so patching the module attribute is enough.
    monkeypatch.setattr(
        "jarvis.core.runtime_refs.get_speech_pipeline", lambda: fake
    )


def test_stuck_active_bar_without_session_starts_session_not_hangup(monkeypatch):
    bar = JarvisBarOverlay()
    bar._mode = "listen"  # stuck active after a wake-lock-rejected wake
    fake = _FakePipeline(session_active=False)
    _patch_pipeline(monkeypatch, fake)

    bar._on_click(_x_glyph(), hovered=True)

    assert fake.hangup_calls == 0   # never the useless trap-hangup
    assert fake.session_calls == 1  # the click escapes the stuck state


def test_live_session_close_x_still_hangs_up(monkeypatch):
    bar = JarvisBarOverlay()
    bar._mode = "listen"
    fake = _FakePipeline(session_active=True)
    _patch_pipeline(monkeypatch, fake)

    bar._on_click(_x_glyph(), hovered=True)

    assert fake.hangup_calls == 1   # a genuine session is still hang-up-able
    assert fake.session_calls == 0
    assert bar._mode == "idle"      # immediate visual acknowledgement


def test_repeated_close_click_does_not_reopen_session(monkeypatch):
    """A rapid second click lands where the X used to be after the optimistic
    collapse; it must not be reinterpreted as an idle-body session start."""
    now = [100.0]
    monkeypatch.setattr(
        "jarvis.ui.jarvisbar.overlay.time.monotonic", lambda: now[0]
    )
    bar = JarvisBarOverlay()
    bar._mode = "listen"
    fake = _FakePipeline(session_active=True)
    _patch_pipeline(monkeypatch, fake)

    bar._on_click(_x_glyph(), hovered=True)
    now[0] += 0.2
    bar._on_click(_x_glyph(), hovered=True)

    assert fake.hangup_calls == 1
    assert fake.session_calls == 0

    # The guard is bounded: after the backend has reached idle, the same area
    # behaves like the normal idle body and can deliberately start a new call.
    fake._session_active = False
    now[0] += 1.0
    bar._on_click(_x_glyph(), hovered=True)
    assert fake.session_calls == 1


def test_missing_accessor_preserves_legacy_hangup(monkeypatch):
    """Fail-safe: an older pipeline without ``is_session_active`` keeps the
    legacy behaviour (trust ``_mode``) so the gate can never silence a real
    hang-up by accident."""

    class _Legacy:
        def __init__(self) -> None:
            self.hangup_calls = 0
            self.session_calls = 0

        def request_hangup(self) -> None:
            self.hangup_calls += 1

        def request_voice_session(self) -> None:
            self.session_calls += 1

        def request_ptt_toggle(self) -> None:  # pragma: no cover - unused here
            ...

    fake = _Legacy()
    monkeypatch.setattr(
        "jarvis.core.runtime_refs.get_speech_pipeline", lambda: fake
    )
    bar = JarvisBarOverlay()
    bar._mode = "listen"

    bar._on_click(_x_glyph(), hovered=True)

    assert fake.hangup_calls == 1
    assert fake.session_calls == 0
