"""BUG-089: the realtime session must never answer its own speaker echo.

The Mac live test (2026-07-18) looped forever: a surface-spoken canned
apology leaked from the speakers into the built-in mic, came back as a
provider-transcribed "user" turn, and the session answered it — two voices
conversing with each other. These tests pin the text-level backstop: every
text the session makes audible is registered with its ``SelfEchoGuard``, and
a final input transcript that is fuzzily nothing but that recent speech is
dropped before ANY turn side effect (no response request, no user
transcript, no barge). German fixture strings quote the runtime voice
product surface (the actual Mac transcript).
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession


class FakeSession:
    session_id = "fake"
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = False

    def __init__(self, events):
        self._events = events
        self.sent_audio = []
        self.tool_results = []
        self.truncated = []
        self.session_updates = []
        self.response_requests = 0
        self.text_inputs = []
        self.interrupts = 0
        self.closed = False

    async def send_audio(self, chunk):
        self.sent_audio.append(chunk)

    async def receive(self):
        for event in self._events:
            yield event
            await asyncio.sleep(0)

    async def update_session(self, *, instructions=None, language=None, tools=None):
        self.session_updates.append(
            {"instructions": instructions, "language": language, "tools": tools}
        )

    async def request_response(self, *, required_tool=None):
        del required_tool
        self.response_requests += 1

    async def send_text(self, text):
        self.text_inputs.append(text)

    async def truncate(self, audio_end_ms):
        self.truncated.append(audio_end_ms)

    async def interrupt(self):
        self.interrupts += 1

    async def send_tool_result(self, call_id, name, result):
        self.tool_results.append((call_id, name, result))

    async def close(self):
        self.closed = True


class AutoResponseFakeSession(FakeSession):
    creates_responses_automatically = True


class FakeProvider:
    name = "fake"
    supports_realtime = True
    input_sample_rate = 16000
    output_sample_rate = 24000
    session_cls = FakeSession

    def __init__(self, events):
        self._events = events
        self.opened_with = None

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = self.session_cls(self._events)
        return self.session


class AutoResponseFakeProvider(FakeProvider):
    session_cls = AutoResponseFakeSession


def _cfg():
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="auto", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime"),
        latency=SimpleNamespace(enabled=False),
    )


async def _run(provider, *, arm=None):
    """Drive one scripted session; return (fake_session, json_messages)."""
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="echo-test",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    if arm is not None:
        arm(sess)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.05)
    await sess.end(reason="test")
    return provider.session, jsons


def _user_transcripts(jsons):
    return [
        message
        for message in jsons
        if message.get("type") == "transcript" and message.get("role") == "user"
    ]


@pytest.mark.asyncio
async def test_canned_phrase_echo_never_becomes_a_turn():
    """THE loop regression: the spoken apology returns garbled as 'user' input."""
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                # Garbled echo of the canned provider-down apology — exactly
                # the Mac loop's fuel.
                text="mein Sprachmodell ist im Moment nicht erreichbar",  # i18n-allow: garbled echo under test
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    fake, jsons = await _run(
        provider,
        arm=lambda sess: sess._surface_speech_message(
            "Tut mir leid, mein Sprachmodell ist im Moment "  # i18n-allow: voice fixture
            "nicht erreichbar. Ich versuche es gleich erneut."  # i18n-allow: voice fixture
        ),
    )
    assert fake.response_requests == 0
    assert fake.text_inputs == []
    assert _user_transcripts(jsons) == []
    # No second apology was triggered by the echo either.
    assert not [m for m in jsons if m.get("type") == "error_spoken"]


@pytest.mark.asyncio
async def test_provider_output_transcript_echo_is_dropped():
    """The provider's own voiced reply, echoed back after the turn, is dropped."""
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="output_transcript_delta",
                text="Moin, bei mir ist alles bestens und ich bin bereit.",  # i18n-allow: voice fixture
            ),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(pcm=b"\x01\x02" * 2_400, sample_rate=24000, timestamp_ns=0),
            ),
            RealtimeEvent(type="turn_complete"),
            RealtimeEvent(
                type="input_transcript",
                text="bei mir ist alles bestens ich bin bereit",  # i18n-allow: garbled echo under test
                is_final=True,
            ),
        ]
    )
    fake, jsons = await _run(provider)
    assert fake.response_requests == 0
    assert _user_transcripts(jsons) == []


@pytest.mark.asyncio
async def test_genuine_user_turn_with_novel_content_passes():
    """Fail-open pin: an answer that ADDS anything is a real turn."""
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Ich will wissen was morgen für ein Tag ist",  # i18n-allow: voice fixture
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    fake, jsons = await _run(
        provider,
        arm=lambda sess: sess._surface_speech_message(
            "Tut mir leid, mein Sprachmodell ist im Moment nicht erreichbar."  # i18n-allow: voice fixture
        ),
    )
    assert fake.response_requests == 1
    assert _user_transcripts(jsons)


@pytest.mark.asyncio
async def test_short_commands_are_never_judged():
    """Sub-3-token turns always reach their handlers, echo window or not."""
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="stopp",  # i18n-allow: voice fixture
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    fake, _jsons = await _run(
        provider,
        arm=lambda sess: sess._surface_speech_message(
            "Soll ich wirklich stoppen und auflegen?"  # i18n-allow: voice fixture
        ),
    )
    assert fake.response_requests == 1


@pytest.mark.asyncio
async def test_echo_drop_interrupts_auto_response_provider():
    """Auto-response adapters get a best-effort interrupt + output withhold."""
    provider = AutoResponseFakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="mein Sprachmodell ist im Moment nicht erreichbar",  # i18n-allow: garbled echo under test
                is_final=True,
            ),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="echo-auto",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    sess._surface_speech_message(
        "Tut mir leid, mein Sprachmodell ist im Moment nicht erreichbar."  # i18n-allow: voice fixture
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.05)
    dropped_flag = sess._drop_provider_output_until_user_turn
    await sess.end(reason="test")
    assert provider.session.interrupts == 1
    assert dropped_flag is True


@pytest.mark.asyncio
async def test_playback_horizon_future_dates_the_guard():
    """Long provider audio arms the guard past the plain wall-clock window."""
    sess = RealtimeVoiceSession(
        session_id="horizon",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(),
        bus=None,
    )
    # 12 s of audio at 24 kHz, streamed far faster than realtime.
    chunk = AudioChunk(pcm=b"\x01\x02" * (24_000 * 3), sample_rate=24000, timestamp_ns=0)
    for _ in range(4):
        await sess._emit_audio(chunk)
    lead_ns = sess._echo_guard.activity_ns - time.time_ns()
    assert lead_ns > int(8e9), "horizon must extend ~12s into the future"


@pytest.mark.asyncio
async def test_playback_horizon_is_capped():
    """A mis-reported sample rate cannot arm the guard for hours."""
    sess = RealtimeVoiceSession(
        session_id="horizon-cap",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(),
        bus=None,
    )
    chunk = AudioChunk(pcm=b"\x01\x02" * 24_000, sample_rate=1, timestamp_ns=0)
    await sess._emit_audio(chunk)
    lead_ns = sess._echo_guard.activity_ns - time.time_ns()
    assert lead_ns <= int(121e9)


@pytest.mark.asyncio
async def test_barge_in_resets_the_horizon():
    """A real barge-in pulls the armed horizon back to 'now'."""
    sess = RealtimeVoiceSession(
        session_id="horizon-reset",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(),
        bus=None,
    )
    chunk = AudioChunk(pcm=b"\x01\x02" * (24_000 * 3), sample_rate=24000, timestamp_ns=0)
    for _ in range(4):
        await sess._emit_audio(chunk)
    await sess._barge_in(interrupt_provider=False)
    lead_ns = sess._echo_guard.activity_ns - time.time_ns()
    assert lead_ns < int(1e9), "horizon must collapse to ~now after a barge"
