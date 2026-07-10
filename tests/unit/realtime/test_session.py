import asyncio

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession


class FakeSession:
    session_id = "fake"

    def __init__(self, events):
        self._events = events
        self.sent_audio = []
        self.tool_results = []
        self.truncated = []
        self.closed = False

    async def send_audio(self, chunk):
        self.sent_audio.append(chunk)

    async def receive(self):
        for ev in self._events:
            yield ev
            await asyncio.sleep(0)

    async def update_session(self, *, instructions=None, language=None):
        pass

    async def truncate(self, audio_end_ms):
        self.truncated.append(audio_end_ms)

    async def interrupt(self):
        pass

    async def send_tool_result(self, call_id, name, result):
        self.tool_results.append((call_id, name, result))

    async def close(self):
        self.closed = True


class FakeProvider:
    name = "fake"
    supports_realtime = True
    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(self, events):
        self._events = events
        self.opened_with = None

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = FakeSession(self._events)
        return self.session


class FailingProvider(FakeProvider):
    name = "failing-family"

    async def open_session(self, cfg):
        raise RuntimeError("simulated depleted credits")


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class FakeToolBridge:
    declarations = (
        {
            "name": "open_app",
            "description": "Open an application.",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string"}},
                "required": ["app_name"],
            },
        },
    )

    def __init__(self):
        self.languages = []
        self.transcripts = []
        self.calls = []
        self.closed = False

    def set_language(self, language):
        self.languages.append(language)

    async def handle_user_transcript(self, text):
        self.transcripts.append(text)

    async def execute(self, *, wire_name, arguments):
        self.calls.append((wire_name, arguments))
        return "open_app", {"success": True, "output": "opened", "error": None}

    async def close(self):
        self.closed = True


def _cfg(*, providers=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers=providers or {}),
        voice=SimpleNamespace(mode="realtime"),
    )


@pytest.mark.asyncio
async def test_open_injects_active_providers_model_and_voice():
    """_open must resolve the model/voice from [brain.providers.<active
    provider's name>], not the dead cfg.voice.realtime_voice read."""
    from types import SimpleNamespace

    providers = {
        "fake": SimpleNamespace(model="gpt-realtime-2.1", voice="echo"),
        "other-provider": SimpleNamespace(model="should-not-be-used", voice="should-not-be-used"),
    }
    sess = RealtimeVoiceSession(
        session_id="s-model-voice",
        send_binary=lambda b: asyncio.sleep(0),
        send_json=lambda m: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(providers=providers),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.02)
    await sess.end(reason="test")

    opened_cfg = sess._provider.opened_with
    assert opened_cfg.model == "gpt-realtime-2.1"
    assert opened_cfg.voice == "echo"


@pytest.mark.asyncio
async def test_open_defaults_to_empty_model_and_voice_when_unset():
    """No [brain.providers.<id>] entry -> "" / "" so the adapter falls back
    to its own hardcoded default (today's behavior, no regression)."""
    sess = RealtimeVoiceSession(
        session_id="s-default",
        send_binary=lambda b: asyncio.sleep(0),
        send_json=lambda m: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.02)
    await sess.end(reason="test")

    opened_cfg = sess._provider.opened_with
    assert opened_cfg.model == ""
    assert opened_cfg.voice == ""


@pytest.mark.asyncio
async def test_handshake_failure_crosses_to_next_provider_family():
    fallback = FakeProvider([])
    fallback.name = "working-family"
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="s-fallback",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        providers=[FailingProvider([]), fallback],
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.end(reason="test")

    assert sess.active_provider == "working-family"
    assert any(message.get("type") == "provider_fallback" for message in jsons)
    assert any(
        message.get("type") == "audio_ready"
        and message.get("provider") == "working-family"
        for message in jsons
    )


@pytest.mark.asyncio
async def test_input_is_resampled_to_active_provider_rate():
    provider = FakeProvider([])
    provider.input_sample_rate = 24_000
    sess = RealtimeVoiceSession(
        session_id="s-resample",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.handle_audio_frame(b"\x01\x00" * 1_600)
    await sess.end(reason="test")

    assert provider.session.sent_audio
    sent = provider.session.sent_audio[0]
    assert sent.sample_rate == 24_000
    assert abs(len(sent.pcm) // 2 - 2_400) <= 2


@pytest.mark.asyncio
async def test_clean_turn_streams_audio_and_transcript():
    events = [
        RealtimeEvent(type="output_transcript_delta", text="Hello there."),
        RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24000, timestamp_ns=0),
        ),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="s1",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await sess.wait_finished()
    await sess.end(reason="test")
    assert any(m.get("type") == "transcript" for m in jsons)
    assert binaries  # audio was released after the clean transcript


@pytest.mark.asyncio
async def test_hard_leak_transcript_drops_audio():
    events = [
        RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24000, timestamp_ns=0),
        ),
        RealtimeEvent(
            type="output_transcript_delta",
            text="Traceback (most recent call last):\n  File a\nValueError: b\n\n",
        ),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="s2",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await sess.wait_finished()
    await sess.end(reason="test")
    # The pre-leak audio was buffered, then dropped when the leak transcript arrived.
    assert binaries == []


@pytest.mark.asyncio
async def test_later_segment_leak_audio_not_emitted():
    # Regression test for the T4 ScrubHoldGate one-chunk-boundary residual:
    # push_audio's "cleared" branch bundles the release-triggering chunk with
    # the previously-buffered one, so a LATER segment's first audio chunk
    # could ride along before its own transcript is scrubbed. The session
    # must flush release_available() right after sending a clean transcript
    # so the gate's _cleared flag never spans into the next segment's audio.
    a1 = b"\x11\x22" * 8
    a2 = b"\x33\x44" * 8
    events = [
        RealtimeEvent(
            type="audio_delta", audio=AudioChunk(pcm=a1, sample_rate=24000, timestamp_ns=0)
        ),
        RealtimeEvent(type="output_transcript_delta", text="A clean first sentence."),
        RealtimeEvent(
            type="audio_delta", audio=AudioChunk(pcm=a2, sample_rate=24000, timestamp_ns=0)
        ),
        RealtimeEvent(
            type="output_transcript_delta",
            text="Traceback (most recent call last):\n  File x\nValueError: y\n\n",
        ),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="s3",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await sess.wait_finished()
    await sess.end(reason="test")
    assert a1 in binaries
    assert a2 not in binaries


@pytest.mark.asyncio
async def test_desktop_session_publishes_effective_provider_and_completed_turn():
    events = [
        RealtimeEvent(type="input_transcript", text="Hello", is_final=True),
        RealtimeEvent(type="output_transcript_delta", text="Hi there."),
        RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24_000, timestamp_ns=0),
        ),
        RealtimeEvent(type="turn_complete"),
    ]
    bus = FakeBus()
    provider = FakeProvider(events)
    provider.name = "working-family"
    sess = RealtimeVoiceSession(
        session_id="desktop-telemetry",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(
            providers={
                "working-family": type(
                    "ProviderConfig", (), {"model": "live-model", "voice": "voice"}
                )()
            }
        ),
        bus=bus,
        surface="desktop",
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    by_name = {type(event).__name__: event for event in bus.events}
    ready = by_name["RealtimeSessionReady"]
    completed = by_name["VoiceTurnCompleted"]
    assert ready.provider == "working-family"
    assert ready.model == "live-model"
    assert ready.surface == "desktop"
    assert completed.tier == "realtime"
    assert completed.provider == "working-family"
    assert completed.model == "live-model"
    assert completed.user_text == "Hello"
    assert completed.jarvis_text == "Hi there."
    assert "VoiceSessionStarted" not in by_name
    assert "VoiceSessionEnded" not in by_name


@pytest.mark.asyncio
async def test_browser_session_start_precedes_realtime_turn_events():
    bus = FakeBus()
    sess = RealtimeVoiceSession(
        session_id="browser-telemetry",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider(
            [RealtimeEvent(type="input_transcript", text="Hello", is_final=True)]
        ),
        config=_cfg(),
        bus=bus,
        surface="browser",
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 48_000})
    await sess.wait_finished()
    await sess.end(reason="ws_closed")

    names = [type(event).__name__ for event in bus.events]
    assert names.index("VoiceSessionStarted") < names.index("RealtimeSessionReady")
    assert names.index("VoiceSessionStarted") < names.index("VoiceTurnStarted")
    assert names[-1] == "VoiceSessionEnded"


@pytest.mark.asyncio
async def test_tool_call_waits_for_final_input_transcript_and_uses_bridge():
    bridge = FakeToolBridge()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="tool_call",
                call_id="call-1",
                tool_name="open_app",
                tool_args={"app_name": "Calculator"},
            ),
            RealtimeEvent(
                type="input_transcript",
                text="Open Calculator",
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="tool-session",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
        tool_bridge=bridge,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert provider.opened_with.tools == bridge.declarations
    assert bridge.transcripts == ["Open Calculator"]
    assert bridge.calls == [("open_app", {"app_name": "Calculator"})]
    assert provider.session.tool_results == [
        (
            "call-1",
            "open_app",
            {"success": True, "output": "opened", "error": None},
        )
    ]
    assert bridge.closed is True


@pytest.mark.asyncio
async def test_untranscribed_tool_call_is_rejected_without_execution():
    bridge = FakeToolBridge()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="tool_call",
                call_id="call-2",
                tool_name="open_app",
                tool_args={"app_name": "Calculator"},
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="tool-no-transcript",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
        tool_bridge=bridge,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert bridge.calls == []
    assert provider.session.tool_results[0][0:2] == ("call-2", "open_app")
    assert provider.session.tool_results[0][2]["success"] is False
