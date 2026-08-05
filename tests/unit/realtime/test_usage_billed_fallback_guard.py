"""The one guarantee that costs real money when it breaks.

A provider that declares ``implicit_usage_fallback_allowed = False`` (today: the
ChatGPT-subscription transport) promises the user that a failed call STOPS
instead of quietly continuing on metered API credentials. The factory turns that
capability into ``allow_classic_fallback`` and both voice surfaces are supposed
to honour it — but until this module the whole promise was one ``if`` on each
surface with nothing asserting it, so a refactor could delete it and leave every
failed subscription call silently billed to the user's own API keys.

Each case is asserted in BOTH directions: the guard must stop the metered path
when it is off AND still allow it when it is on, so a test that passes by
accident (or a guard hardwired to one answer) fails here.

The harnesses are imported from the two suites that own those surfaces rather
than copied — a private copy of ``_pipe`` would drift out of sync with the real
``SpeechPipeline`` attributes and start passing for the wrong reason.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.browser_voice.route as route_mod
import jarvis.speech.pipeline as pipeline_mod
from jarvis.browser_voice.route import (
    _USAGE_FALLBACK_DISABLED_DETAIL,
    browser_voice_ws,
)
from jarvis.sessions.constants import HANGUP_ERROR
from jarvis.ui.web.missions_auth import register_token, revoke_token
from tests.unit.browser_voice.test_route import (
    _VALID_TOKEN,
    _FakeWS,
    _RecSession,
    _state,
)
from tests.unit.speech.test_realtime_mode import _pipe, _SilentMic


@pytest.fixture(autouse=True)
def _registered_browser_token():
    """The browser socket authenticates even on loopback."""
    register_token(_VALID_TOKEN)
    try:
        yield
    finally:
        revoke_token(_VALID_TOKEN)


class _FailingDesktopSession:
    """A realtime session that dies in its handshake, like a refused plan."""

    def __init__(self, *, allow_classic_fallback: bool) -> None:
        self.allow_classic_fallback = allow_classic_fallback
        self.hangup_reason = ""
        self.end_reason = ""

    async def handle_control(self, _message) -> None:
        raise RuntimeError("simulated subscription transport failure")

    async def handle_audio_frame(self, _pcm: bytes) -> None:
        return None

    async def wait_finished(self) -> None:
        return None

    async def end(self, *, reason: str = "") -> None:
        self.end_reason = reason


# ── Desktop surface ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fallback_allowed", "expected"),
    [(False, HANGUP_ERROR), (True, None)],
)
@pytest.mark.asyncio
async def test_desktop_unbuildable_realtime_stops_instead_of_billing_api(
    monkeypatch: pytest.MonkeyPatch,
    fallback_allowed: bool,
    expected: str | None,
) -> None:
    """No credential-ready provider: end the call, never run metered classic.

    ``None`` is the pipeline's request to replay this call through the classic
    STT/TTS/brain stack — every one of which spends an API key.
    """
    pipe = _pipe()
    monkeypatch.setattr(
        "jarvis.realtime.factory.realtime_implicit_usage_fallback_allowed",
        lambda _cfg: fallback_allowed,
    )
    monkeypatch.setattr(
        "jarvis.realtime.factory.build_realtime_session",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic()
    )

    reason = await asyncio.wait_for(pipe._active_realtime_session(), timeout=2.0)

    assert reason == expected


@pytest.mark.parametrize(
    ("fallback_allowed", "expected"),
    [(False, HANGUP_ERROR), (True, None)],
)
@pytest.mark.asyncio
async def test_desktop_failed_handshake_stops_instead_of_billing_api(
    monkeypatch: pytest.MonkeyPatch,
    fallback_allowed: bool,
    expected: str | None,
) -> None:
    """The session exists but its handshake dies — the live plan-limit shape.

    The surface must read the capability off the SESSION here (the factory
    already resolved it), not re-derive it from anything else.
    """
    pipe = _pipe()
    built: dict[str, _FailingDesktopSession] = {}

    def _build(**_kwargs):
        session = _FailingDesktopSession(allow_classic_fallback=fallback_allowed)
        built["session"] = session
        return session

    # Deliberately the OPPOSITE of the session's own answer: whichever value the
    # surface honours, it must be the one the built session carries.
    monkeypatch.setattr(
        "jarvis.realtime.factory.realtime_implicit_usage_fallback_allowed",
        lambda _cfg: not fallback_allowed,
    )
    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic()
    )

    reason = await asyncio.wait_for(pipe._active_realtime_session(), timeout=2.0)

    assert reason == expected
    assert built["session"].allow_classic_fallback is fallback_allowed


# ── Browser surface ──────────────────────────────────────────────────────────


def _browser_socket(state) -> _FakeWS:
    return _FakeWS(
        [
            {
                "type": "websocket.receive",
                "text": '{"type":"audio_start","sample_rate":48000}',
            }
        ],
        state=state,
    )


@pytest.mark.asyncio
async def test_browser_failed_realtime_closes_instead_of_billing_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    classic = _RecSession()

    class _SubscriptionOnlyRealtime(_RecSession):
        is_realtime = True
        allow_classic_fallback = False

        async def handle_control(self, _msg: dict) -> None:
            raise RuntimeError("simulated subscription transport failure")

    failed = _SubscriptionOnlyRealtime()
    state = _state(classic)
    state.config.voice = SimpleNamespace(mode="realtime")
    monkeypatch.setattr(route_mod, "_build_browser_session", lambda **_kwargs: failed)
    ws = _browser_socket(state)

    await browser_voice_ws(ws)

    assert failed.ended is True
    # The metered stack was never handed the turn.
    assert classic.controls == []
    assert classic.audio == []
    assert {"type": "mode_fallback", "mode": "pipeline"} not in ws.sent_json
    assert any(
        message.get("type") == "provider_error"
        and message.get("error") == _USAGE_FALLBACK_DISABLED_DETAIL
        for message in ws.sent_json
    )
    assert ws.closed == (1011, "automatic API fallback disabled")


@pytest.mark.asyncio
async def test_browser_failed_realtime_still_crosses_when_fallback_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror: an API-billed primary keeps its classic safety net."""
    classic = _RecSession()

    class _MeteredRealtime(_RecSession):
        is_realtime = True
        allow_classic_fallback = True

        async def handle_control(self, _msg: dict) -> None:
            raise RuntimeError("simulated metered transport failure")

    failed = _MeteredRealtime()
    state = _state(classic)
    state.config.voice = SimpleNamespace(mode="realtime")
    monkeypatch.setattr(route_mod, "_build_browser_session", lambda **_kwargs: failed)
    ws = _browser_socket(state)

    await browser_voice_ws(ws)

    assert failed.ended is True
    assert classic.controls == [{"type": "audio_start", "sample_rate": 48_000}]
    assert {"type": "mode_fallback", "mode": "pipeline"} in ws.sent_json
