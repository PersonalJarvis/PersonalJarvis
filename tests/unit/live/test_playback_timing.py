"""Provider source intervals survive through to the sole browser audio owner."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.live.config import LiveConfig
from jarvis.live.playback import AudioTimedFrame, SpeechTimingFrame, source_interval, utf16_length
from jarvis.live.session import LiveVoiceSession
from jarvis.live.state import LiveLedger
from jarvis.live.subscription import SubscriptionLiveVoiceSession
from jarvis.plugins.realtime.openai_subscription_live import OpenAISubscriptionLiveConnection
from tests.fakes.fake_subscription_live_wire import SDP, FakeSubscriptionSocket
from tests.fakes.fake_subscription_session import subscription_provider


@pytest.mark.asyncio
async def test_real_subscription_envelope_keeps_source_times_instead_of_receive_times(tmp_path):
    raw = [
        {
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(bytes(9600)).decode(),
            "start_ms": 600,
            "end_ms": 800,
        },
        {
            "type": "output_transcript.added",
            "start_ms": 600,
            "end_ms": 800,
            "item": {"id": "item-1", "type": "text", "text": "One"},
        },
        {
            "type": "output_transcript.added",
            "start_ms": 1200,
            "end_ms": 1400,
            "item": {"id": "item-2", "type": "text", "text": " two"},
        },
        {
            "type": "turn.done",
            "turn": {
                "id": "turn-1",
                "role": "assistant",
                "start_ms": 600,
                "end_ms": 1400,
                "transcript": "One two",
            },
        },
    ]
    conn = OpenAISubscriptionLiveConnection(
        FakeSubscriptionSocket(raw), session_id="rtc_test", answer_sdp=SDP
    )
    messages = []

    async def send(value):
        messages.append(value)

    provider = subscription_provider()
    provider.source_timed_audio = True
    session = SubscriptionLiveVoiceSession(
        session_id="voice-test",
        providers=[provider],
        config=SimpleNamespace(
            live=LiveConfig(configured=True), brain=SimpleNamespace(reply_language="en")
        ),
        send_json=send,
        send_binary=send,
    )
    session._connection = conn
    session._ledger = LiveLedger(tmp_path / "timing.sqlite3")
    session._tools = SimpleNamespace(user_text="")
    try:
        await session._transport_ready(SDP)
        assert messages[-1]["output_transport"] == "timed_pcm"
        for _ in raw:
            event = await conn.receive()
            if event["type"].startswith("session.output_transcript"):
                assert event["timestamp_source"] == "source_audio"
                assert event["start_ms"] == 600
            await session._event(event)
        audio = next(m for m in messages if m["type"] == "audio_timed")
        assert AudioTimedFrame.model_validate(audio).start_ms == 600
        cues = [
            SpeechTimingFrame.model_validate(m) for m in messages if m["type"] == "speech_timing"
        ]
        assert [(c.start_ms, c.end_ms, c.char_start, c.char_end) for c in cues] == [
            (600, 800, 0, 3),
            (1200, 1400, 3, 7),
        ]
        assert cues[0].line_id == cues[1].line_id
        assert cues[1].text == "One two"
        # A final transcript is not a playback acknowledgement.
        assert len(cues) == 2
        await session._clear_playback(wait_for_provider=True)
        before = len(messages)
        await session._event(
            {
                "type": "session.output_audio.delta",
                "delta": "AAAA",
                "start_ms": 1400,
                "end_ms": 1600,
            }
        )
        assert len(messages) == before
        await session._event({"type": "output_audio_buffer.cleared"})
        await session._event(
            {
                "type": "session.output_audio.delta",
                "delta": "AAAA",
                "start_ms": 1600,
                "end_ms": 1800,
            }
        )
        assert messages[-1]["epoch"] == 2
        await session._clear_playback(wait_for_provider=True)
        await session._caption(event)  # A cancelled turn may end without a clear acknowledgement.
        assert not session._awaiting_output_clear
        await session._event(
            {
                "type": "session.output_audio.delta",
                "delta": "AAAAAA==",
                "start_ms": 1800,
                "end_ms": 2000,
            }
        )
        assert messages[-1]["epoch"] == 4
    finally:
        session._ledger.close()


def test_intervals_and_offsets_are_browser_compatible():
    assert utf16_length("A\U0001f600 B") == 5
    assert source_interval(0, 200)
    for start, end in [(True, 200), (0, False), (100, 100), (-1, 20), (0, float("inf"))]:
        assert not source_interval(start, end)
    frame = SpeechTimingFrame(
        epoch=0,
        line_id="line",
        text="A\U0001f600 B",
        char_start=0,
        char_end=5,
        start_ms=0,
        end_ms=200,
    )
    assert frame.char_end == 5


@pytest.mark.asyncio
async def test_untimed_legacy_transcript_is_not_claimed_as_audio_timing():
    conn = OpenAISubscriptionLiveConnection(
        FakeSubscriptionSocket(
            [
                {"type": "output_transcript.added", "item": {"text": "Legacy"}},
            ]
        ),
        session_id="rtc_test",
        answer_sdp=SDP,
    )
    event = await conn.receive()
    assert event["timestamp_source"] == "local_receive_clock"
    assert "fragment_start_ms" not in event


def test_shared_browser_wire_fixture_validates_without_changing_values():
    fixture = Path(__file__).parents[2] / "fixtures" / "voice_playback_contract.json"
    audio, text = json.loads(fixture.read_text(encoding="utf-8"))
    assert AudioTimedFrame.model_validate(audio).model_dump() == audio
    assert SpeechTimingFrame.model_validate(text).model_dump() == text


@pytest.mark.asyncio
async def test_public_live_uses_the_same_timed_output_contract():
    messages = []

    async def send(value):
        messages.append(value)

    session = LiveVoiceSession(
        session_id="public-test",
        providers=[SimpleNamespace(name="public-test", source_timed_audio=True)],
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        send_json=send,
        send_binary=send,
    )
    session._connection = SimpleNamespace(answer_sdp="answer")
    session._tools = SimpleNamespace(user_text="")
    session._ledger = SimpleNamespace(append=lambda _: True)
    await session._event(
        {
            "type": "session.output_transcript.delta",
            "delta": "Hello",
            "start_ms": 100,
            "end_ms": 300,
        }
    )
    await session._event(
        {"type": "session.output_audio.delta", "delta": "AAAA", "start_ms": 100, "end_ms": 300}
    )
    assert next(m for m in messages if m["type"] == "speech_timing")["char_end"] == 5
    assert messages[-1]["type"] == "audio_timed"


def test_bad_metadata_does_not_log_provider_payload(caplog):
    from jarvis.live.playback import playback_frame

    assert (
        playback_frame(
            SpeechTimingFrame,
            epoch=0,
            line_id="",
            text="private-payload-sentinel",
            char_start=0,
            char_end=5,
            start_ms=0,
            end_ms=10,
        )
        is None
    )
    assert "private-payload-sentinel" not in caplog.text
    assert "metadata was discarded" in caplog.text
