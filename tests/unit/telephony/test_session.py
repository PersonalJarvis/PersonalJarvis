"""TelephonyCallSession turn-loop tests with fakes (no models, no socket)."""

from __future__ import annotations

import base64
import math
import struct

import pytest

from jarvis.telephony.audio import TWILIO_SAMPLE_RATE, pcm16_to_ulaw
from jarvis.telephony.constants import CALL_COMPLETED, CALL_NO_AUDIO
from jarvis.telephony.session import HANGUP_RE, TelephonyCallSession
from tests.fakes.fake_telephony_stack import FakeBrain, FakeSTT, FakeTTS

# asyncio_mode = "auto" (pyproject) runs async tests with no marker; sync tests
# in this module (the hangup-regex parametrizations) must NOT carry the asyncio
# mark, so we do not set a module-level pytestmark.


def _ulaw_frame(amp: int, ms: int = 20, freq: int = 300) -> str:
    """One base64 mu-law 8 kHz frame (what Twilio sends)."""
    n = TWILIO_SAMPLE_RATE * ms // 1000
    if amp == 0:
        pcm = b"\x00\x00" * n
    else:
        pcm = b"".join(
            struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / TWILIO_SAMPLE_RATE)))
            for i in range(n)
        )
    return base64.b64encode(pcm16_to_ulaw(pcm)).decode("ascii")


class _Sink:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, msg: dict) -> None:
        self.messages.append(msg)

    @property
    def media_frames(self) -> list[dict]:
        return [m for m in self.messages if m.get("event") == "media"]

    @property
    def clear_events(self) -> list[dict]:
        return [m for m in self.messages if m.get("event") == "clear"]


def _make_session(sink, *, stt=None, brain=None, tts=None, **kw) -> TelephonyCallSession:
    params = {
        "call_sid": "CA1",
        "stream_sid": "MZ1",
        "send": sink.send,
        "stt": stt or FakeSTT(["Wie spät ist es?"]),
        "brain": brain or FakeBrain("Es ist vierzehn Uhr."),
        "tts": tts or FakeTTS(ms_per_char=2),
        "language_code": "de-DE",
        "max_call_seconds": 600,
    }
    params.update(kw)
    return TelephonyCallSession(**params)


async def _drive_one_utterance(session: TelephonyCallSession) -> None:
    """Feed silence -> speech -> silence so the endpointer fires one turn,
    then await the spawned turn task."""
    import asyncio

    # The session uses a fast endpointer; configure it for the test.
    session._endpointer.silence_ms = 100
    session._endpointer.min_speech_ms = 60
    for _ in range(2):
        await session.handle_media(_ulaw_frame(0))
    for _ in range(8):
        await session.handle_media(_ulaw_frame(15000))
    for _ in range(10):
        await session.handle_media(_ulaw_frame(0))
    # Let the turn task complete.
    for _ in range(200):
        await asyncio.sleep(0)
        if session.turns >= 1:
            break
        await asyncio.sleep(0.01)


async def test_full_turn_produces_outbound_media_frames():
    sink = _Sink()
    session = _make_session(sink)
    await _drive_one_utterance(session)
    assert session.turns == 1
    assert len(sink.media_frames) > 0
    # Every media frame carries the streamSid and a base64 payload.
    for frame in sink.media_frames:
        assert frame["streamSid"] == "MZ1"
        assert frame["media"]["payload"]
        base64.b64decode(frame["media"]["payload"])  # decodes cleanly


async def test_greeting_is_spoken():
    sink = _Sink()
    session = _make_session(sink, greeting="Willkommen bei Jarvis.")
    n = await session.speak_greeting()
    assert n > 0
    assert len(sink.media_frames) == n


async def test_brain_receives_transcript():
    sink = _Sink()
    brain = FakeBrain("Antwort.")
    session = _make_session(sink, stt=FakeSTT(["Was ist die Uhrzeit?"]), brain=brain)
    await _drive_one_utterance(session)
    assert brain.prompts == ["Was ist die Uhrzeit?"]


async def test_hangup_phrase_ends_call_before_brain():
    sink = _Sink()
    brain = FakeBrain("should not be called")
    session = _make_session(sink, stt=FakeSTT(["Auflegen bitte."]), brain=brain)
    await _drive_one_utterance(session)
    assert session.ended
    assert session.status == CALL_COMPLETED
    assert session.end_reason == "hangup_phrase"
    assert brain.prompts == []  # brain never reached


@pytest.mark.parametrize(
    "phrase",
    ["auflegen", "leg auf", "tschüss", "hang up", "goodbye", "beenden"],
)
def test_hangup_regex_matches_closing_phrases(phrase):
    assert HANGUP_RE.search(phrase)


@pytest.mark.parametrize("phrase", ["wie geht es dir", "erzähl mir was", "danke schön"])
def test_hangup_regex_ignores_normal_speech(phrase):
    assert HANGUP_RE.search(phrase) is None


async def test_empty_transcript_skips_turn():
    sink = _Sink()
    brain = FakeBrain("x")
    session = _make_session(sink, stt=FakeSTT([""]), brain=brain)
    await _drive_one_utterance(session)
    assert session.turns == 0
    assert brain.prompts == []


async def test_no_audio_status_when_call_silent():
    sink = _Sink()
    session = _make_session(sink)
    # No media handled at all -> end() should downgrade to no_audio.
    await session.end(reason="socket_closed", status=CALL_COMPLETED)
    assert session.status == CALL_NO_AUDIO


async def test_barge_in_sends_clear_event():
    sink = _Sink()
    session = _make_session(sink)
    # Simulate mid-playback: mark speaking and feed a loud frame.
    session._speaking = True
    await session.handle_media(_ulaw_frame(20000))
    assert len(sink.clear_events) == 1
    assert sink.clear_events[0]["streamSid"] == "MZ1"


async def test_empty_brain_response_falls_back_to_spoken_phrase():
    sink = _Sink()
    session = _make_session(sink, brain=FakeBrain(""))
    await _drive_one_utterance(session)
    # A fallback phrase was synthesized -> outbound frames exist despite empty brain.
    assert session.turns == 1
    assert len(sink.media_frames) > 0


async def test_per_call_brain_history_is_isolated():
    """Two sessions with their own FakeBrain do not share history."""
    s1 = _make_session(_Sink(), brain=FakeBrain("a"))
    s2 = _make_session(_Sink(), brain=FakeBrain("b"))
    await _drive_one_utterance(s1)
    # s2's brain saw nothing from s1.
    assert s2._brain.prompts == []
    assert s1._brain.prompts != []


def test_time_cap_detection():
    session = _make_session(_Sink(), max_call_seconds=0)
    assert session.check_time_cap() is True


async def test_brain_end_call_sentinel_ends_call_after_speaking():
    sink = _Sink()
    brain = FakeBrain("Auf Wiedersehen, Alex. [[END_CALL]]")
    tts = FakeTTS(ms_per_char=2)
    session = _make_session(
        sink,
        stt=FakeSTT(["Ich glaube wir sind durch"]),  # not an explicit regex command
        brain=brain,
        tts=tts,
    )

    await _drive_one_utterance(session)

    assert brain.prompts == ["Ich glaube wir sind durch"]  # brain WAS reached
    assert session.ended
    assert session.status == CALL_COMPLETED
    assert session.end_reason == "hangup_phrase"
    assert tts.calls, "the farewell must be spoken before hanging up"
    assert all("[[END_CALL]]" not in text for (text, _lang) in tts.calls)
