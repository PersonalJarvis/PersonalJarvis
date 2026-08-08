"""Half-duplex mute release is gated on PHYSICAL playback where possible.

Provider-frame silence only proves the provider stopped sending; the desktop
surface still has ~180 ms of jitter reserve plus the device drain audibly
playing, and releasing the microphone into that tail fed the reply's
remainder back in as user speech on open speakers. Pinned here: a surface
playback probe defers the release while audio is physically audible, the
veto is bounded by the alert threshold (never a new stuck-mute class), a
probe-less surface pays a conservative drain margin instead, and a broken
probe degrades to the heuristic — reported, not swallowed.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from jarvis.realtime.session import (
    _HALF_DUPLEX_MUTE_ALERT_S,
    _HALF_DUPLEX_NO_PROBE_DRAIN_MARGIN_S,
    _HALF_DUPLEX_SILENT_RELEASE_S,
    RealtimeVoiceSession,
)

RATE = 24_000


class _IdleWire:
    session_id = "playback-release-wire"
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = False

    async def send_audio(self, chunk: Any) -> None:
        del chunk

    async def receive(self):  # noqa: ANN201 - async generator, protocol shape
        if False:  # pragma: no cover - an intentionally empty stream
            yield None

    async def close(self) -> None:
        return None


class _Provider:
    name = "playback-release"
    supports_realtime = True
    input_sample_rate = RATE
    output_sample_rate = RATE

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _IdleWire:
        del config
        return _IdleWire()


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


def _session() -> RealtimeVoiceSession:
    return RealtimeVoiceSession(
        session_id="playback-release",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[_Provider()],
        config=_config(),
        bus=None,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=RATE,
    )


def _arm_mute(
    session: RealtimeVoiceSession, *, muted_s: float, silent_s: float
) -> None:
    now = time.monotonic()
    session._output_active = True
    session._half_duplex_muted_since = now - muted_s
    session._last_output_audio_at = now - silent_s


def test_probe_says_drained_releases_at_the_base_silence_window() -> None:
    session = _session()
    session.set_playback_probe(lambda: False)
    _arm_mute(
        session,
        muted_s=3.0,
        silent_s=_HALF_DUPLEX_SILENT_RELEASE_S + 0.1,
    )
    session._note_half_duplex_mute()
    assert session._output_active is False
    assert session._mute_emergency_releases == 1


def test_probe_still_active_defers_the_release() -> None:
    session = _session()
    session.set_playback_probe(lambda: True)
    _arm_mute(
        session,
        muted_s=3.0,
        silent_s=_HALF_DUPLEX_SILENT_RELEASE_S + 0.5,
    )
    session._note_half_duplex_mute()
    assert session._output_active is True, (
        "the reply is still physically audible; reopening the microphone now "
        "would feed its remainder back in"
    )
    assert session._mute_emergency_releases == 0


def test_probe_veto_is_bounded_by_the_alert_threshold() -> None:
    # A latched probe must never create a new stuck-mute class: past the
    # alert threshold the emergency release runs regardless of the probe.
    session = _session()
    session.set_playback_probe(lambda: True)
    _arm_mute(
        session,
        muted_s=_HALF_DUPLEX_MUTE_ALERT_S + 0.5,
        silent_s=_HALF_DUPLEX_SILENT_RELEASE_S + 0.5,
    )
    session._note_half_duplex_mute()
    assert session._output_active is False
    assert session._mute_emergency_releases == 1


def test_without_a_probe_the_drain_margin_extends_the_window() -> None:
    session = _session()
    # Silent longer than the base window but inside the drain margin: the
    # surface's prebuffer/device drain may still be audible, keep the mute.
    _arm_mute(
        session,
        muted_s=4.0,
        silent_s=_HALF_DUPLEX_SILENT_RELEASE_S + 0.2,
    )
    session._note_half_duplex_mute()
    assert session._output_active is True

    # Past base window plus margin the heuristic release fires as before.
    _arm_mute(
        session,
        muted_s=4.0,
        silent_s=(
            _HALF_DUPLEX_SILENT_RELEASE_S
            + _HALF_DUPLEX_NO_PROBE_DRAIN_MARGIN_S
            + 0.2
        ),
    )
    session._note_half_duplex_mute()
    assert session._output_active is False
    assert session._mute_emergency_releases == 1


def test_a_raising_probe_degrades_to_the_heuristic_release() -> None:
    session = _session()

    def _broken() -> bool:
        raise RuntimeError("probe died")

    session.set_playback_probe(_broken)
    _arm_mute(
        session,
        muted_s=4.0,
        silent_s=(
            _HALF_DUPLEX_SILENT_RELEASE_S
            + _HALF_DUPLEX_NO_PROBE_DRAIN_MARGIN_S
            + 0.2
        ),
    )
    session._note_half_duplex_mute()
    assert session._playback_active_probe is None, (
        "a raising probe is disabled for the rest of the call"
    )
    assert session._output_active is False, (
        "the heuristic release still works after the probe failure"
    )


def test_set_playback_probe_rejects_non_callables() -> None:
    session = _session()
    session.set_playback_probe(None)
    assert session._playback_active_probe is None
    session.set_playback_probe("not-a-callable")  # type: ignore[arg-type]
    assert session._playback_active_probe is None
