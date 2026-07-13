import asyncio

import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.events import (
    LatencyPhase,
    LatencySpan,
    ResponseGenerated,
    SpeechSpoken,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)
from jarvis.core.protocols import AudioChunk, ToolResult
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
        self.required_tools = []
        self.text_inputs = []
        self.interrupts = 0
        self.closed = False

    async def send_audio(self, chunk):
        self.sent_audio.append(chunk)

    async def receive(self):
        for ev in self._events:
            yield ev
            await asyncio.sleep(0)

    async def update_session(self, *, instructions=None, language=None, tools=None):
        self.session_updates.append(
            {"instructions": instructions, "language": language, "tools": tools}
        )

    async def request_response(self, *, required_tool=None):
        self.response_requests += 1
        self.required_tools.append(required_tool)

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


class TextResultGatedSession(FakeSession):
    """Wait for an injected text update before yielding its spoken response."""

    def __init__(self, events):
        super().__init__(events)
        self._text_sent = asyncio.Event()

    async def receive(self):
        await self._text_sent.wait()
        async for event in super().receive():
            yield event

    async def send_text(self, text):
        await super().send_text(text)
        self._text_sent.set()


class TextResultGatedProvider(FakeProvider):
    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = TextResultGatedSession(self._events)
        return self.session


class ToolResultGatedSession(FakeSession):
    """Hold final model output until every scripted tool result has arrived."""

    def __init__(self, before_results, after_results, expected_results):
        super().__init__([])
        self._before_results = before_results
        self._after_results = after_results
        self._expected_results = expected_results
        self._result_sent = asyncio.Event()

    async def receive(self):
        for event in self._before_results:
            yield event
            await asyncio.sleep(0)
        while len(self.tool_results) < self._expected_results:
            await self._result_sent.wait()
            self._result_sent.clear()
        for event in self._after_results:
            yield event
            await asyncio.sleep(0)

    async def send_tool_result(self, call_id, name, result):
        await super().send_tool_result(call_id, name, result)
        self._result_sent.set()


class ToolResultGatedProvider(FakeProvider):
    def __init__(self, before_results, after_results, expected_results=1):
        super().__init__([])
        self._before_results = before_results
        self._after_results = after_results
        self._expected_results = expected_results

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = ToolResultGatedSession(
            self._before_results,
            self._after_results,
            self._expected_results,
        )
        return self.session


class AutomaticDelegateSession(FakeSession):
    """Emit one speculative native turn, then wait for trusted text input."""

    creates_responses_automatically = True

    def __init__(self, before_result, after_result):
        super().__init__([])
        self._before_result = before_result
        self._after_result = after_result
        self._trusted_text_sent = asyncio.Event()

    async def receive(self):
        for event in self._before_result:
            yield event
            await asyncio.sleep(0)
        await self._trusted_text_sent.wait()
        for event in self._after_result:
            yield event
            await asyncio.sleep(0)

    async def send_text(self, text):
        await super().send_text(text)
        self._trusted_text_sent.set()


class AutomaticDelegateProvider(FakeProvider):
    def __init__(self, before_result, after_result):
        super().__init__([])
        self._before_result = before_result
        self._after_result = after_result

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = AutomaticDelegateSession(
            self._before_result,
            self._after_result,
        )
        return self.session


class FailingProvider(FakeProvider):
    name = "failing-family"

    async def open_session(self, cfg):
        raise RuntimeError("simulated depleted credits")


class LeakyFailingProvider(FakeProvider):
    name = "leaky-family"

    async def open_session(self, cfg):
        raise RuntimeError("api_key=sk-proj-abcdefghijklmnopqrstuvwxyz123456")


class UnavailableProvider(FakeProvider):
    name = "unavailable-family"

    async def can_open_duplex_session(self):
        return False


class SlowOpeningProvider(FakeProvider):
    name = "slow-family"

    async def open_session(self, cfg):
        await asyncio.Event().wait()


class SlowAudioSession(FakeSession):
    async def send_audio(self, chunk):
        del chunk
        await asyncio.Event().wait()


class SlowAudioProvider(FakeProvider):
    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = SlowAudioSession(self._events)
        return self.session


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


def _cfg(
    *,
    providers=None,
    reply_language="en",
    stt_language="auto",
    latency_enabled=True,
):
    from types import SimpleNamespace

    return SimpleNamespace(
        brain=SimpleNamespace(
            reply_language=reply_language,
            providers=providers or {},
        ),
        stt=SimpleNamespace(language=stt_language),
        voice=SimpleNamespace(mode="realtime"),
        latency=SimpleNamespace(enabled=latency_enabled),
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
    messages = []
    sess = RealtimeVoiceSession(
        session_id="s-model-voice",
        send_binary=lambda b: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
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
    assert "Realtime engine, provider fake, model gpt-realtime-2.1" in opened_cfg.instructions
    ready = next(message for message in messages if message["type"] == "audio_ready")
    assert ready["provider"] == "fake"
    assert ready["model"] == "gpt-realtime-2.1"


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
async def test_capability_probe_failure_crosses_to_next_provider_family():
    fallback = FakeProvider([])
    fallback.name = "working-family"
    sess = RealtimeVoiceSession(
        session_id="probe-fallback",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[UnavailableProvider([]), fallback],
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.end(reason="test")

    assert sess.active_provider == "working-family"


@pytest.mark.asyncio
async def test_handshake_timeout_preserves_budget_for_next_family(monkeypatch):
    import jarvis.realtime.session as session_module

    monkeypatch.setattr(session_module, "_PROVIDER_HANDSHAKE_TOTAL_TIMEOUT_S", 0.2)
    fallback = FakeProvider([])
    fallback.name = "working-family"
    messages = []
    sess = RealtimeVoiceSession(
        session_id="timeout-fallback",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        providers=[SlowOpeningProvider([]), fallback],
        config=_cfg(),
        bus=None,
    )

    await asyncio.wait_for(
        sess.handle_control({"type": "audio_start", "sample_rate": 16_000}),
        timeout=0.5,
    )
    await sess.end(reason="test")

    assert sess.active_provider == "working-family"
    fallback_status = next(
        item for item in messages if item.get("type") == "provider_fallback"
    )
    assert "handshake exceeded" in fallback_status["error"]


@pytest.mark.asyncio
async def test_fallback_status_redacts_credentials_from_provider_errors():
    fallback = FakeProvider([])
    messages = []
    sess = RealtimeVoiceSession(
        session_id="redacted-fallback",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        providers=[LeakyFailingProvider([]), fallback],
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.end(reason="test")

    fallback_status = next(
        message for message in messages if message.get("type") == "provider_fallback"
    )
    assert "abcdefghijklmnopqrstuvwxyz" not in fallback_status["error"]
    assert "<redacted:" in fallback_status["error"]


@pytest.mark.asyncio
async def test_stream_error_redacts_credentials_before_browser_status():
    messages = []
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="error",
                error="Bearer abcdefghijklmnopqrstuvwxyz123456",
            )
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="redacted-stream-error",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    error_status = next(
        message for message in messages if message.get("type") == "provider_error"
    )
    assert "abcdefghijklmnopqrstuvwxyz" not in error_status["error"]
    assert "<redacted:bearer_token>" in error_status["error"]


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
async def test_final_transcript_sets_turn_language_before_requesting_response():
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Como esta el clima hoy",
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="language-before-response",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language="auto"),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert provider.opened_with.language == "en"
    assert provider.opened_with.language_is_pinned is False
    assert "language of the user's current spoken turn" in (
        provider.opened_with.instructions
    )
    assert provider.session.session_updates[-1]["language"] == "es"
    assert "Reply only in Spanish for this turn" in (
        provider.session.session_updates[-1]["instructions"]
    )
    assert provider.session.response_requests == 1


@pytest.mark.asyncio
async def test_missing_final_transcript_still_requests_a_response_without_tools():
    bridge = FakeToolBridge()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="tool_call",
                call_id="unsafe-without-transcript",
                tool_name="open_app",
                tool_args={"app_name": "Calculator"},
            ),
            RealtimeEvent(
                type="input_transcript",
                text="",
                is_final=True,
                error="transcription failed",
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="missing-transcript",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language="auto", stt_language="de"),
        bus=None,
        tool_bridge=bridge,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert provider.session.session_updates[-1]["language"] == "de"
    assert provider.session.response_requests == 1
    assert bridge.calls == []
    assert provider.session.tool_results[0][0] == "unsafe-without-transcript"
    assert provider.session.tool_results[0][2]["success"] is False


@pytest.mark.asyncio
async def test_empty_successful_final_does_not_open_or_request_a_turn():
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="",
                is_final=True,
                item_id="empty-input",
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="empty-success",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert provider.session.response_requests == 0
    assert sess._turn_index == 0


@pytest.mark.asyncio
async def test_duplicate_final_input_item_requests_exactly_one_response():
    duplicate = RealtimeEvent(
        type="input_transcript",
        text="Tell me once",
        is_final=True,
        item_id="input-1",
    )
    provider = FakeProvider(
        [duplicate, RealtimeEvent(type="turn_complete"), duplicate]
    )
    sess = RealtimeVoiceSession(
        session_id="duplicate-input",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert provider.session.response_requests == 1
    assert sess._turn_index == 1


@pytest.mark.asyncio
async def test_idle_barge_in_does_not_send_invalid_provider_cancel():
    jsons: list[dict[str, object]] = []
    provider = FakeProvider([])
    sess = RealtimeVoiceSession(
        session_id="provider-interrupt",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.handle_control({"type": "barge_in"})
    await sess.end(reason="test")

    assert provider.session.interrupts == 0
    assert {"type": "tts_cancel"} in jsons


@pytest.mark.asyncio
async def test_repeated_barge_in_interrupts_active_provider_only_once():
    provider = FakeProvider([])
    sess = RealtimeVoiceSession(
        session_id="active-provider-interrupt",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    sess._output_active = True
    sess._output_samples_sent = 2_400

    await sess.handle_control({"type": "barge_in"})
    await sess.handle_control({"type": "barge_in"})
    await sess.end(reason="test")

    assert provider.session.interrupts == 1
    assert provider.session.truncated == [100]


@pytest.mark.asyncio
async def test_audio_send_timeout_marks_realtime_session_failed(monkeypatch):
    import jarvis.realtime.session as session_module

    monkeypatch.setattr(session_module, "_AUDIO_SEND_TIMEOUT_S", 0.01)
    sess = RealtimeVoiceSession(
        session_id="audio-send-timeout",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=SlowAudioProvider([]),
        config=_cfg(),
        bus=None,
        browser_sample_rate=16_000,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    with pytest.raises(RuntimeError, match="stopped accepting microphone audio"):
        await sess.handle_audio_frame(b"\x00\x01" * 16)

    assert sess.failed is True
    assert "2.0s" not in sess.failure_detail
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_audio_without_transcript_is_cancelled_fail_closed():
    class _DelayedCompletionSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x01\x02" * 8,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            await asyncio.sleep(0.05)
            yield RealtimeEvent(type="turn_complete")

    class _DelayedCompletionProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _DelayedCompletionSession([])
            return self.session

    binaries = []
    messages = []
    provider = _DelayedCompletionProvider([])
    sess = RealtimeVoiceSession(
        session_id="missing-output-transcript",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert binaries == []
    # response.done already closed the provider generation; fail closed locally
    # without sending a now-invalid response.cancel.
    assert provider.session.interrupts == 0
    assert sum(item.get("type") == "error_spoken" for item in messages) == 1


@pytest.mark.asyncio
async def test_concurrent_transcript_lag_does_not_cancel_clean_output():
    """Audio deltas may legitimately lead their matching transcript delta."""

    first = b"\x01\x02" * 8
    middle = b"\x03\x04" * 8
    tail = b"\x05\x06" * 8

    class _LaggedTranscriptSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(type="output_transcript_delta", text="A safe answer")
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(pcm=first, sample_rate=24_000, timestamp_ns=0),
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(pcm=middle, sample_rate=24_000, timestamp_ns=0),
            )
            # Realtime delta streams are concurrent rather than one-to-one. The
            # former 250 ms timer cancelled normal output during this gap.
            await asyncio.sleep(0.3)
            yield RealtimeEvent(type="output_transcript_delta", text=" continues.")
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(pcm=tail, sample_rate=24_000, timestamp_ns=0),
            )
            yield RealtimeEvent(type="turn_complete")

    class _LaggedTranscriptProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _LaggedTranscriptSession([])
            return self.session

    binaries = []
    messages = []
    provider = _LaggedTranscriptProvider([])
    sess = RealtimeVoiceSession(
        session_id="lagged-clean-transcript",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert binaries == [first, middle, tail]
    assert provider.session.interrupts == 0
    assert not any(item.get("type") == "error_spoken" for item in messages)


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
async def test_latency_and_voice_events_share_one_fresh_trace_per_turn():
    events = []
    for index in range(2):
        events.extend(
            [
                RealtimeEvent(
                    type="input_transcript",
                    text=f"Question {index}",
                    is_final=True,
                ),
                RealtimeEvent(
                    type="output_transcript_delta",
                    text=f"Answer {index}.",
                ),
                RealtimeEvent(
                    type="audio_delta",
                    audio=AudioChunk(
                        pcm=b"\x01\x02" * 8,
                        sample_rate=24_000,
                        timestamp_ns=0,
                    ),
                ),
                RealtimeEvent(type="turn_complete"),
            ]
        )
    bus = FakeBus()
    sess = RealtimeVoiceSession(
        session_id="trace-reset",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")
    await asyncio.sleep(0.02)

    started = [event for event in bus.events if isinstance(event, VoiceTurnStarted)]
    completed = [
        event for event in bus.events if isinstance(event, VoiceTurnCompleted)
    ]
    spans = [event for event in bus.events if isinstance(event, LatencySpan)]

    assert len(started) == len(completed) == 2
    assert started[0].trace_id != started[1].trace_id
    assert [item.trace_id for item in completed] == [
        item.trace_id for item in started
    ]
    assert [item.turn_id for item in started] == [
        str(item.trace_id) for item in started
    ]
    expected_phases = {
        LatencyPhase.REALTIME_INPUT_COMMITTED,
        LatencyPhase.REALTIME_ROUTING_DECISION,
        LatencyPhase.REALTIME_FIRST_TRANSCRIPT,
        LatencyPhase.REALTIME_FIRST_AUDIO,
        LatencyPhase.REALTIME_TURN_COMPLETE,
    }
    for turn in started:
        turn_spans = [span for span in spans if span.trace_id == turn.trace_id]
        assert {span.phase for span in turn_spans} == expected_phases
        assert all("session_id=trace-reset" in span.detail for span in turn_spans)


@pytest.mark.asyncio
async def test_missing_turn_complete_latency_phase_cannot_fail_voice_turn(monkeypatch):
    """A stale telemetry enum must never close an otherwise healthy session."""
    import jarvis.telemetry.latency as latency_module

    class LegacyLatencyPhase:
        REALTIME_INPUT_COMMITTED = LatencyPhase.REALTIME_INPUT_COMMITTED
        REALTIME_ROUTING_DECISION = LatencyPhase.REALTIME_ROUTING_DECISION
        REALTIME_FIRST_TRANSCRIPT = LatencyPhase.REALTIME_FIRST_TRANSCRIPT

    monkeypatch.setattr(latency_module, "LatencyPhase", LegacyLatencyPhase)
    bus = FakeBus()
    messages = []
    sess = RealtimeVoiceSession(
        session_id="stale-latency-enum",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=FakeProvider(
            [
                RealtimeEvent(
                    type="input_transcript",
                    text="Keep this conversation open",
                    is_final=True,
                ),
                RealtimeEvent(
                    type="output_transcript_delta",
                    text="The session is still active.",
                ),
                RealtimeEvent(type="turn_complete"),
            ]
        ),
        config=_cfg(),
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert sess.failed is False
    assert any(isinstance(event, VoiceTurnCompleted) for event in bus.events)
    assert {message["type"] for message in messages} >= {
        "audio_ready",
        "turn_complete",
    }
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_broken_latency_tracker_cannot_fail_voice_turn(monkeypatch):
    """Optional tracker initialization must fail open for the voice session."""
    import jarvis.telemetry.latency as latency_module

    class BrokenLatencyTracker:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("simulated telemetry version skew")

    monkeypatch.setattr(latency_module, "LatencyTracker", BrokenLatencyTracker)
    bus = FakeBus()
    sess = RealtimeVoiceSession(
        session_id="broken-latency-tracker",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider(
            [
                RealtimeEvent(
                    type="input_transcript",
                    text="Keep listening after this turn",
                    is_final=True,
                ),
                RealtimeEvent(
                    type="output_transcript_delta",
                    text="I am still listening.",
                ),
                RealtimeEvent(type="turn_complete"),
            ]
        ),
        config=_cfg(),
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert sess.failed is False
    assert any(isinstance(event, VoiceTurnCompleted) for event in bus.events)
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_disabled_realtime_latency_emits_no_spans():
    bus = FakeBus()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Hello",
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="latency-disabled",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(latency_enabled=False),
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")
    await asyncio.sleep(0)

    assert not any(isinstance(event, LatencySpan) for event in bus.events)


@pytest.mark.asyncio
async def test_idle_session_renders_external_update_as_realtime_spoken_track():
    provider = TextResultGatedProvider(
        [
            RealtimeEvent(
                type="output_transcript_delta",
                text="The research mission is ready.",
            ),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x01\x02" * 8,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    bus = FakeBus()
    sess = RealtimeVoiceSession(
        session_id="external-update",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=bus,
        surface="desktop",
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    accepted = await sess.deliver_announcement(
        text="Research completed successfully.",
        language="en",
        spoken_kind="subagent",
        detail="artifact: report.md",
    )
    await sess.wait_finished()
    await sess.end(reason="test")

    assert accepted is True
    assert "Research completed successfully." in provider.session.text_inputs[0]
    spoken = [event for event in bus.events if isinstance(event, SpeechSpoken)]
    assert len(spoken) == 1
    assert spoken[0].text == "The research mission is ready."
    assert spoken[0].language == "en"
    assert spoken[0].spoken_kind == "subagent"
    assert spoken[0].detail == "artifact: report.md"
    assert not any(isinstance(event, ResponseGenerated) for event in bus.events)
    assert not any(isinstance(event, VoiceTurnCompleted) for event in bus.events)


@pytest.mark.asyncio
async def test_busy_realtime_session_refuses_external_update_for_classic_fallback():
    provider = FakeProvider([])
    sess = RealtimeVoiceSession(
        session_id="busy-update",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    sess._turn_id = "active-user-turn"

    accepted = await sess.deliver_announcement(
        text="The mission finished.",
        language="en",
        spoken_kind="completion",
    )

    assert accepted is False
    assert provider.session.text_inputs == []
    await sess.end(reason="test")


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

    # Bridge tools are declared unchanged; the session appends its own
    # end_call lifecycle declaration last.
    assert provider.opened_with.tools[: len(bridge.declarations)] == bridge.declarations
    assert provider.opened_with.tools[-1]["name"] == "end_call"
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


@pytest.mark.asyncio
async def test_untranscribed_tool_call_times_out_and_unblocks_provider(monkeypatch):
    monkeypatch.setattr("jarvis.realtime.session._TOOL_TRANSCRIPT_WAIT_S", 0.01)
    bridge = FakeToolBridge()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="tool_call",
                call_id="call-timeout",
                tool_name="open_app",
                tool_args={"app_name": "Calculator"},
            )
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="tool-transcript-timeout",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
        tool_bridge=bridge,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.03)
    await sess.end(reason="test")

    assert bridge.calls == []
    assert provider.session.tool_results[0][0] == "call-timeout"
    assert provider.session.tool_results[0][2]["success"] is False


# --- Voice hang-up parity (regex + end_call tool) --------------------------


def _hangup_jsons(jsons):
    return [m for m in jsons if m.get("type") == "hangup"]


@pytest.mark.asyncio
async def test_hangup_phrase_finishes_session_with_voice_pattern():
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="bitte auflegen",  # i18n-allow: German hang-up phrase under test
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="hangup-regex",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason=sess.hangup_reason)

    assert sess.hangup_reason == "voice_pattern"
    assert _hangup_jsons(jsons)
    # The explicit closing command ends the call BEFORE any model response,
    # exactly like the classic pre-brain HANGUP_RE path.
    assert provider.session.response_requests == 0


@pytest.mark.asyncio
async def test_gemini_fragmented_final_chunks_accumulate_to_hangup():
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="auf", is_final=True),  # i18n-allow
            RealtimeEvent(type="input_transcript", text="legen", is_final=True),  # i18n-allow
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="hangup-fragments",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason=sess.hangup_reason)

    assert sess.hangup_reason == "voice_pattern"
    assert _hangup_jsons(jsons)


@pytest.mark.asyncio
async def test_hangup_accumulator_resets_at_turn_boundary():
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="auf", is_final=True),  # i18n-allow
            RealtimeEvent(type="turn_complete"),
            RealtimeEvent(
                type="input_transcript",
                text="legen wir los",  # i18n-allow: must NOT join across turns
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="hangup-turn-boundary",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert sess.hangup_reason == ""
    assert _hangup_jsons(jsons) == []


@pytest.mark.asyncio
async def test_end_call_tool_finishes_after_turn_complete():
    bridge = FakeToolBridge()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="danke das war alles",  # i18n-allow: polite closing under test
                is_final=True,
            ),
            RealtimeEvent(type="tool_call", call_id="c-end", tool_name="end_call"),
            RealtimeEvent(type="output_transcript_delta", text="Goodbye!"),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="hangup-end-call",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
        tool_bridge=bridge,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason=sess.hangup_reason)

    # end_call is session lifecycle: acknowledged to the model, never routed
    # through the tool bridge, and the hang-up waits for the goodbye turn.
    assert ("c-end", "end_call", {"success": True}) in provider.session.tool_results
    assert bridge.calls == []
    assert sess.hangup_reason == "voice_pattern"
    hangups = _hangup_jsons(jsons)
    assert hangups
    turn_completes = [m for m in jsons if m.get("type") == "turn_complete"]
    assert turn_completes, "the model finishes its goodbye before the hang-up"


@pytest.mark.asyncio
async def test_ordinary_speech_does_not_hang_up():
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="wie ist das wetter heute",  # i18n-allow: ordinary speech guard
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="hangup-guard",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert sess.hangup_reason == ""
    assert _hangup_jsons(jsons) == []
    assert provider.session.response_requests == 1


@pytest.mark.asyncio
async def test_language_switch_mistranscript_reaches_realtime_provider():
    """The live ``auf jetzt`` false positive must not end the session."""
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text=(
                    "Antworte auf jetzt nur noch auf Englisch."  # i18n-allow: bug transcript
                ),
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="language-switch-hangup-guard",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert sess.hangup_reason == ""
    assert _hangup_jsons(jsons) == []
    assert provider.session.response_requests == 1


# --- Tool-role directive in session instructions ----------------------------


@pytest.mark.asyncio
async def test_instructions_carry_tool_role_when_bridge_active():
    """A session WITH action tools must tell the model to use them — the
    live defect was a model that had ~25 declared functions but instructions
    that never mentioned a tool role, so it claimed it could not act."""
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = RealtimeVoiceSession(
        session_id="tool-role-on",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
        tool_bridge=FakeToolBridge(),
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    instructions = provider.opened_with.instructions
    assert "call the matching function" in instructions
    assert "Jarvis-Agent spawn" in instructions


@pytest.mark.asyncio
async def test_instructions_omit_tool_role_without_bridge():
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = RealtimeVoiceSession(
        session_id="tool-role-off",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert "call the matching function" not in provider.opened_with.instructions


# --- Delegate tool mode (jarvis_action -> classic router-brain turn) --------


class FakeBrain:
    """Recording callable brain with a generate(text, **kwargs) contract."""

    def __init__(self, replies=("done",), error=None, gate=None, bus=None):
        self.calls = []
        self._replies = list(replies)
        self._error = error
        self._gate = gate
        self._bus = bus
        self.cancelled = False

    async def generate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        try:
            if self._gate is not None:
                await self._gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self._error is not None:
            raise self._error
        reply = self._replies.pop(0) if self._replies else "done"
        if self._bus is not None and kwargs.get("publish_response", True):
            await self._bus.publish(ResponseGenerated(text=reply, language="en"))
        return reply

    async def __call__(self, text):
        return await self.generate(text)


class _StubTool:
    name = "open_app"
    description = "Open an application."
    risk_tier = "monitor"
    schema = {"type": "object", "properties": {}}

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("Realtime must execute through the supervisor gateway")


class _StubExecutor:
    async def execute(self, _tool, _arguments, **_kwargs):
        return ToolResult(success=True, output="opened")


def _delegate_cfg(tool_mode=None):
    cfg = _cfg()
    if tool_mode is not None:
        cfg.voice.realtime_tool_mode = tool_mode
    return cfg


def _tool_names(opened_cfg):
    return [d["name"] for d in opened_cfg.tools]


def _session(
    provider,
    *,
    brain=None,
    tool_bridge=None,
    tool_mode=None,
    jsons=None,
    binaries=None,
    bus=None,
):
    return RealtimeVoiceSession(
        session_id="delegate-test",
        send_binary=(
            (lambda data: binaries.append(data) or asyncio.sleep(0))
            if binaries is not None
            else (lambda _data: asyncio.sleep(0))
        ),
        send_json=(
            (lambda m: jsons.append(m) or asyncio.sleep(0))
            if jsons is not None
            else (lambda _m: asyncio.sleep(0))
        ),
        provider=provider,
        config=_delegate_cfg(tool_mode),
        bus=bus,
        brain=brain,
        tool_bridge=tool_bridge,
    )


@pytest.fixture
def wire_supervisor_gateway():
    previous = runtime_refs.get_supervisor_tool_gateway()

    def _wire(brain, executor):
        brain._tool_executor = executor
        gateway = BrainSupervisorToolGateway(brain)
        runtime_refs.set_supervisor_tool_gateway(gateway)

    yield _wire
    runtime_refs.set_supervisor_tool_gateway(previous)


@pytest.mark.asyncio
async def test_delegate_mode_declares_single_action_function():
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=FakeBrain())

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert _tool_names(provider.opened_with) == ["jarvis_action", "end_call"]
    assert "jarvis_action" in provider.opened_with.instructions
    assert "Wiki or personal memory" in provider.opened_with.instructions
    assert "MCPs" in provider.opened_with.instructions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "Who is my best friend?",
        "Was weißt du über mich?",  # i18n-allow: German speech-input fixture
        "Which MCPs and CLIs are installed?",
        "What is in my Gmail inbox?",
        "Read the SAP customer record.",
        "Which pull requests are open today?",
        "What did I have open on my computer today?",
        "Use the morning routine skill.",
        "Call Anna.",
        "Click Save in the browser.",
        "¿Qué herramientas están conectadas?",  # i18n-allow: Spanish speech-input fixture
        "Write that to the wiki.",
        "Write the last transcript to the wiki.",
        "Kannst du bitte mein Wiki-System eintragen, "  # i18n-allow: German speech-input fixture
        "dass ich morgen nach San Francisco "  # i18n-allow: German speech-input fixture
        "reisen will?",  # i18n-allow: German speech-input fixture
    ],
)
async def test_local_evidence_turns_run_deterministic_jarvis_action(utterance):
    brain = FakeBrain()
    provider = FakeProvider(
        [RealtimeEvent(type="input_transcript", text=utterance, is_final=True)]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.12)

    assert provider.session.required_tools == []
    assert brain.calls[0][0] == utterance
    assert provider.session.text_inputs
    assert "<trusted_action_result>" in provider.session.text_inputs[-1]
    update = provider.session.session_updates[-1]["instructions"]
    assert "orchestrator is handling this current turn" in update
    await sess.end(reason="test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "What is the capital of France?",
        "What is SAP?",
        "How do I open a file in Python?",
        "I sent you an email yesterday.",
    ],
)
async def test_general_knowledge_turn_keeps_native_realtime_answering(utterance):
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text=utterance,
                is_final=True,
            )
        ]
    )
    sess = _session(provider, brain=FakeBrain())

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert provider.session.required_tools == [None]
    await sess.end(reason="test")


class _ConfirmAwaitingBrain(FakeBrain):
    """FakeBrain that reports a pending two-turn voice confirmation."""

    def __init__(self, *args, pending=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending = pending

    def has_pending_voice_confirm(self):
        return self.pending


@pytest.mark.asyncio
async def test_pending_voice_confirm_forces_deterministic_delegation():
    """A bare yes/no answer must reach the brain's confirmation resume.

    "Ja, gerne." matches no planner action vocabulary, so without the
    pending-confirm probe the armed ask-tier action would depend on the
    provider voluntarily calling jarvis_action.
    """
    brain = _ConfirmAwaitingBrain(replies=("The email was sent.",))
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Ja, gerne.",  # i18n-allow: German speech-input fixture
                is_final=True,
            )
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.12)

    assert brain.calls[0][0] == "Ja, gerne."  # i18n-allow: fixture echo
    assert provider.session.text_inputs
    assert "<trusted_action_result>" in provider.session.text_inputs[-1]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_bare_answer_without_pending_confirm_stays_native():
    brain = _ConfirmAwaitingBrain(pending=False)
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Ja, gerne.",  # i18n-allow: German speech-input fixture
                is_final=True,
            )
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert brain.calls == []
    assert provider.session.required_tools == [None]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_automatic_provider_wiki_turn_runs_brain_without_tool_call():
    brain = FakeBrain(replies=("The Wiki entry was saved.",))
    speculative_audio = AudioChunk(
        pcm=b"\x01\x02" * 8,
        sample_rate=24_000,
        timestamp_ns=0,
    )
    provider = AutomaticDelegateProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Write the travel plan to my wiki.",
                is_final=True,
            ),
            RealtimeEvent(type="audio_delta", audio=speculative_audio),
            RealtimeEvent(
                type="output_transcript_delta",
                text="I do not have access to your Wiki.",
            ),
            RealtimeEvent(type="audio_delta", audio=speculative_audio),
            RealtimeEvent(type="turn_complete"),
        ],
        [
            RealtimeEvent(
                type="output_transcript_delta",
                text="The Wiki entry was saved.",
            ),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    jsons = []
    binaries = []
    sess = _session(
        provider,
        brain=brain,
        jsons=jsons,
        binaries=binaries,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert [call[0] for call in brain.calls] == [
        "Write the travel plan to my wiki."
    ]
    assistant_text = "".join(
        str(message.get("text", ""))
        for message in jsons
        if message.get("role") == "assistant"
    )
    assert assistant_text == "The Wiki entry was saved."
    assert provider.session.tool_results == []
    assert len(provider.session.text_inputs) == 1
    assert binaries == []
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_multi_final_transcript_waits_for_provider_turn_boundary():
    brain = FakeBrain(replies=("Stored the complete travel plan.",))

    class _DelayedFinalSession(AutomaticDelegateSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Write this to my wiki",
                is_final=True,
            )
            await asyncio.sleep(0.12)
            yield RealtimeEvent(
                type="input_transcript",
                text="that I travel to San Francisco tomorrow",
                is_final=True,
            )
            yield RealtimeEvent(type="turn_complete")
            await self._trusted_text_sent.wait()
            yield RealtimeEvent(type="turn_complete")

    class _DelayedFinalProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _DelayedFinalSession([], [])
            return self.session

    provider = _DelayedFinalProvider([])
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.2)

    assert brain.calls[0][0] == (
        "Write this to my wiki that I travel to San Francisco tomorrow"
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_barge_in_detaches_late_delegate_result_from_new_turn():
    gate = asyncio.Event()
    dispatch_started = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatch_started.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(replies=("Old Wiki action completed.",), gate=gate)

    class _BargeSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Write this to my wiki.",
                is_final=True,
            )
            await dispatch_started.wait()
            yield RealtimeEvent(type="speech_started")
            yield RealtimeEvent(
                type="input_transcript",
                text="What time is it?",
                is_final=True,
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x01\x02" * 8,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )

    class _BargeProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _BargeSession([])
            return self.session

    provider = _BargeProvider([])
    binaries = []
    sess = _session(provider, brain=brain, binaries=binaries)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    new_turn_id = sess._turn_id
    gate.set()
    await asyncio.sleep(0.1)

    assert new_turn_id
    assert sess._last_user_text == "What time is it?"
    assert provider.session.text_inputs == []
    assert provider.session.tool_results == []
    assert binaries == []
    await sess.end(reason="test")


class _InterjectionSession(FakeSession):
    """Interrupt a running delegate, finish the interjection, stay connected."""

    def __init__(self, events, *, dispatch_started, interjection_done, delivered):
        super().__init__(events)
        self._dispatch_started = dispatch_started
        self._interjection_done = interjection_done
        self._delivered = delivered

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="Write this to my wiki.",
            is_final=True,
        )
        await self._dispatch_started.wait()
        # The action is slow, so the user speaks into the silence.
        yield RealtimeEvent(type="speech_started")
        yield RealtimeEvent(
            type="input_transcript",
            text="Hello?",
            is_final=True,
        )
        yield RealtimeEvent(type="turn_complete")
        # Resuming past the yield proves the pump has handled turn_complete.
        self._interjection_done.set()
        await self._delivered.wait()

    async def send_text(self, text):
        await super().send_text(text)
        self._delivered.set()


class _InterjectionProvider(FakeProvider):
    def __init__(self, *, dispatch_started, interjection_done, delivered):
        super().__init__([])
        self._dispatch_started = dispatch_started
        self._interjection_done = interjection_done
        self._delivered = delivered

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = _InterjectionSession(
            [],
            dispatch_started=self._dispatch_started,
            interjection_done=self._interjection_done,
            delivered=self._delivered,
        )
        return self.session


@pytest.mark.asyncio
async def test_action_result_that_outlived_its_turn_is_still_spoken():
    """An executed action must never be reported only by the model's promise."""
    gate = asyncio.Event()
    dispatch_started = asyncio.Event()
    interjection_done = asyncio.Event()
    delivered = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatch_started.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(
        replies=("Stored on your page: flight to San Francisco tomorrow.",),
        gate=gate,
    )
    provider = _InterjectionProvider(
        dispatch_started=dispatch_started,
        interjection_done=interjection_done,
        delivered=delivered,
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(interjection_done.wait(), timeout=2)
    assert provider.session.text_inputs == []  # never inside the live turn

    gate.set()
    await asyncio.wait_for(delivered.wait(), timeout=2)
    await sess.wait_finished()

    spoken = provider.session.text_inputs[-1]
    assert "Stored on your page: flight to San Francisco tomorrow." in spoken
    assert "<trusted_action_result>" in spoken
    assert "earlier request" in spoken
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_turn_after_a_pending_action_may_not_claim_an_outcome():
    gate = asyncio.Event()
    dispatch_started = asyncio.Event()
    instructions_seen = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatch_started.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(replies=("Stored.",), gate=gate)

    class _PendingSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Write this to my wiki.",
                is_final=True,
            )
            await dispatch_started.wait()
            yield RealtimeEvent(type="speech_started")
            yield RealtimeEvent(
                type="input_transcript",
                text="Hello?",
                is_final=True,
            )
            instructions_seen.set()
            await gate.wait()

    class _PendingProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _PendingSession([])
            return self.session

    provider = _PendingProvider([])
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(instructions_seen.wait(), timeout=2)

    update = provider.session.session_updates[-1]["instructions"]
    assert "still being executed" in update
    gate.set()
    await sess.wait_finished()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_direct_mode_builds_bridge_from_brain(wire_supervisor_gateway):
    brain = FakeBrain()
    brain._tools = {"open_app": _StubTool()}
    wire_supervisor_gateway(brain, _StubExecutor())
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=brain, tool_mode="direct")

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    names = _tool_names(provider.opened_with)
    assert "open_app" in names
    assert "end_call" in names
    assert "jarvis_action" not in names


@pytest.mark.asyncio
async def test_direct_mode_pushes_live_brain_tool_replacements_to_provider(
    wire_supervisor_gateway,
):
    brain = FakeBrain()
    brain._tools = {"old_tool": _StubTool()}
    wire_supervisor_gateway(brain, _StubExecutor())
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Which tools are available?",
                is_final=True,
            )
        ]
    )
    sess = _session(provider, brain=brain, tool_mode="direct")

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    brain._tools = {"new_tool": _StubTool()}
    await sess.wait_finished()

    assert _tool_names(provider.opened_with) == ["old_tool", "end_call"]
    updated_tools = provider.session.session_updates[-1]["tools"]
    assert [item["name"] for item in updated_tools] == ["new_tool", "end_call"]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_explicit_bridge_wins_over_delegate_mode():
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=FakeBrain(), tool_bridge=FakeToolBridge())

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    names = _tool_names(provider.opened_with)
    assert "open_app" in names
    assert "jarvis_action" not in names


@pytest.mark.asyncio
async def test_delegate_directive_orders_a_function_call_for_private_memory():
    """The model is the fallback whenever the deterministic gate misses."""
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=FakeBrain())

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    instructions = provider.opened_with.instructions
    # It must never again be told to sit on its hands for those turns.
    assert "Do not answer or call a function for those turns" not in instructions
    assert "Wiki or personal memory" in instructions
    assert "garbled follow-up" in instructions
    assert "Never announce that you are going to" in instructions


@pytest.mark.asyncio
async def test_gate_miss_lets_the_model_reach_the_wiki_through_jarvis_action():
    """A vague follow-up the planner cannot classify must still reach the brain."""
    from jarvis.brain.turn_planner import plan_turn

    utterance = "Was steht da drin?"  # i18n-allow: German speech-input fixture
    assert plan_turn(utterance).requires_orchestrator is False

    brain = FakeBrain(replies=("Your wiki holds pages about you and Lukas.",))
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text=utterance,
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="c-1",
                tool_name="jarvis_action",
                tool_args={"request": "What is in my wiki?"},
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    assert brain.calls[0][0] == utterance
    assert provider.session.tool_results[0][2] == {
        "success": True,
        "spoken_reply": "Your wiki holds pages about you and Lukas.",
    }
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_call_dispatches_raw_transcript_with_voice_confirm():
    brain = FakeBrain(replies=("Settings are open.",))
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="please open the settings view",
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="c-1",
                tool_name="jarvis_action",
                tool_args={"request": "Open settings"},
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    assert brain.calls == [
        (
            "please open the settings view",
            {
                "allow_voice_confirm": True,
                "prefer_tool_model": True,
                "publish_response": False,
                "use_history": False,
                "history_override": (),
            },
        )
    ]
    assert provider.session.tool_results == [
        (
            "c-1",
            "jarvis_action",
            {"success": True, "spoken_reply": "Settings are open."},
        )
    ]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_uses_only_bounded_current_realtime_history():
    brain = FakeBrain(replies=("Saved.",))
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="The launch code name is Aurora.",
                is_final=True,
            ),
            RealtimeEvent(type="output_transcript_delta", text="Understood."),
            RealtimeEvent(type="turn_complete"),
            RealtimeEvent(
                type="input_transcript",
                text="Write that to the wiki.",
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="history-1",
                tool_name="jarvis_action",
                tool_args={"request": "Write that to the wiki."},
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    text, kwargs = brain.calls[0]
    assert text == "Write that to the wiki."
    assert kwargs["use_history"] is False
    assert [(item.role, item.content) for item in kwargs["history_override"]] == [
        ("user", "The launch code name is Aurora."),
        ("assistant", "Understood."),
    ]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_native_mission_claim_is_withheld_for_trusted_brain_result():
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Start an agent to create the report.",
                is_final=True,
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text="I started the mission successfully.",
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=FakeBrain(), jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assistant_text = "".join(
        str(message.get("text", ""))
        for message in jsons
        if message.get("role") == "assistant"
    )
    assert "started the mission" not in assistant_text
    assert provider.session.tool_results == []
    assert provider.session.text_inputs
    assert "<trusted_action_result>\ndone\n" in provider.session.text_inputs[-1]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_turn_publishes_only_the_spoken_realtime_response():
    bus = FakeBus()
    brain = FakeBrain(replies=("Internal action result.",), bus=bus)
    provider = ToolResultGatedProvider(
        [
            RealtimeEvent(type="input_transcript", text="open settings", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="canonical-1",
                tool_name="jarvis_action",
                tool_args={"request": "open settings"},
            ),
        ],
        [
            RealtimeEvent(
                type="output_transcript_delta",
                text="The settings view is open.",
            ),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    sess = _session(provider, brain=brain, bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    responses = [event for event in bus.events if isinstance(event, ResponseGenerated)]
    completed = next(event for event in bus.events if isinstance(event, VoiceTurnCompleted))
    assert [event.text for event in responses] == ["The settings view is open."]
    assert brain.calls[0][1]["publish_response"] is False
    assert completed.jarvis_text == "The settings view is open."
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_conversation_turn_keeps_the_session_response_event():
    bus = FakeBus()
    brain = FakeBrain(bus=bus)
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="hello", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text="Hello there."),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain, bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    responses = [event for event in bus.events if isinstance(event, ResponseGenerated)]
    assert [event.text for event in responses] == ["Hello there."]
    assert brain.calls == []
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_direct_tool_turn_keeps_the_session_response_event(
    wire_supervisor_gateway,
):
    bus = FakeBus()
    brain = FakeBrain(bus=bus)
    brain._tools = {"open_app": _StubTool()}
    wire_supervisor_gateway(brain, _StubExecutor())
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="open it", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="direct-1",
                tool_name="open_app",
                tool_args={},
            ),
            RealtimeEvent(type="output_transcript_delta", text="It is open."),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain, tool_mode="direct", bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    responses = [event for event in bus.events if isinstance(event, ResponseGenerated)]
    assert [event.text for event in responses] == ["It is open."]
    assert provider.session.tool_results[0][2]["success"] is True
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_multiple_delegate_calls_coalesce_to_one_brain_turn():
    bus = FakeBus()
    brain = FakeBrain(replies=("First result.", "Second result."), bus=bus)
    provider = ToolResultGatedProvider(
        [
            RealtimeEvent(type="input_transcript", text="do both", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="multi-1",
                tool_name="jarvis_action",
                tool_args={"request": "first action"},
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="multi-2",
                tool_name="jarvis_action",
                tool_args={"request": "second action"},
            ),
        ],
        [
            RealtimeEvent(type="output_transcript_delta", text="Both are done."),
            RealtimeEvent(type="turn_complete"),
        ],
        expected_results=2,
    )
    sess = _session(provider, brain=brain, bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    responses = [event for event in bus.events if isinstance(event, ResponseGenerated)]
    assert [event.text for event in responses] == ["Both are done."]
    assert len(brain.calls) == 1
    assert all(call[1]["publish_response"] is False for call in brain.calls)
    assert len(provider.session.tool_results) == 2
    assert {
        result[2]["spoken_reply"] for result in provider.session.tool_results
    } == {"First result."}
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_failure_leaves_the_spoken_error_as_the_only_response():
    bus = FakeBus()
    brain = FakeBrain(error=RuntimeError("simulated failure"), bus=bus)
    provider = ToolResultGatedProvider(
        [
            RealtimeEvent(type="input_transcript", text="do it", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="failure-1",
                tool_name="jarvis_action",
                tool_args={"request": "do it"},
            ),
        ],
        [
            RealtimeEvent(
                type="output_transcript_delta",
                text="I could not complete that action.",
            ),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    sess = _session(provider, brain=brain, bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    responses = [event for event in bus.events if isinstance(event, ResponseGenerated)]
    assert [event.text for event in responses] == ["I could not complete that action."]
    assert provider.session.tool_results[0][2]["success"] is False
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_empty_brain_result_cannot_claim_action_success():
    brain = FakeBrain(replies=("",))
    provider = ToolResultGatedProvider(
        [
            RealtimeEvent(type="input_transcript", text="do it", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="empty-result-1",
                tool_name="jarvis_action",
                tool_args={"request": "do it"},
            ),
        ],
        [RealtimeEvent(type="turn_complete")],
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    result = provider.session.tool_results[0][2]
    assert result["success"] is False
    assert "no grounded result" in result["error"]
    assert result["spoken_reply"]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_timeout_cancels_brain_and_cannot_publish_late(monkeypatch):
    monkeypatch.setattr("jarvis.realtime.session._DELEGATE_TIMEOUT_S", 0.01)
    bus = FakeBus()
    brain = FakeBrain(gate=asyncio.Event(), bus=bus)
    provider = ToolResultGatedProvider(
        [
            RealtimeEvent(type="input_transcript", text="slow action", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="timeout-1",
                tool_name="jarvis_action",
                tool_args={"request": "slow action"},
            ),
        ],
        [
            RealtimeEvent(type="output_transcript_delta", text="That action timed out."),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    sess = _session(provider, brain=brain, bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    responses = [event for event in bus.events if isinstance(event, ResponseGenerated)]
    assert [event.text for event in responses] == ["That action timed out."]
    assert brain.cancelled is True
    assert provider.session.tool_results[0][2]["success"] is False
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_empty_spoken_answer_uses_one_internal_reply_fallback():
    bus = FakeBus()
    brain = FakeBrain(replies=("The action completed.",), bus=bus)
    provider = ToolResultGatedProvider(
        [
            RealtimeEvent(type="input_transcript", text="do it", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="empty-1",
                tool_name="jarvis_action",
                tool_args={"request": "do it"},
            ),
        ],
        [RealtimeEvent(type="turn_complete")],
    )
    sess = _session(provider, brain=brain, bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    responses = [event for event in bus.events if isinstance(event, ResponseGenerated)]
    completed = next(event for event in bus.events if isinstance(event, VoiceTurnCompleted))
    assert [event.text for event in responses] == ["The action completed."]
    assert completed.jarvis_text == ""
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_does_not_block_pump():
    gate = asyncio.Event()
    brain = FakeBrain(replies=("Done.",), gate=gate)
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="do the thing", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="c-2",
                tool_name="jarvis_action",
                tool_args={"request": "do the thing"},
            ),
            RealtimeEvent(type="output_transcript_delta", text="Working on it."),
        ]
    )
    sess = _session(provider, brain=brain, jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    # The pump processed the later transcript while the brain turn still hangs.
    assert any(
        m.get("type") == "transcript" and m.get("role") == "assistant" for m in jsons
    )
    assert provider.session.tool_results == []

    gate.set()
    await asyncio.sleep(0.02)
    assert provider.session.tool_results
    assert provider.session.tool_results[0][2]["spoken_reply"] == "Done."
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_turn_complete_waits_for_slow_delegate_task_on_same_turn():
    gate = asyncio.Event()
    brain = FakeBrain(replies=("Completed on the original turn.",), gate=gate)
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="do it", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="slow-same-turn",
                tool_name="jarvis_action",
                tool_args={"request": "do it"},
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    original_turn_id = sess._turn_id

    assert original_turn_id
    assert original_turn_id in sess._delegate_turns
    assert sess._last_user_text == "do it"
    assert sess._turn_has_pending_delegate(original_turn_id) is True

    gate.set()
    await asyncio.sleep(0.02)

    assert sess._turn_id == original_turn_id
    assert sess._delegate_turns[original_turn_id].last_reply == (
        "Completed on the original turn."
    )
    assert provider.session.tool_results[0][0] == "slow-same-turn"
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_timeout_sends_honest_failure(monkeypatch):
    monkeypatch.setattr("jarvis.realtime.session._DELEGATE_TIMEOUT_S", 0.05)
    gate = asyncio.Event()  # never set -- the brain turn hangs
    brain = FakeBrain(gate=gate)
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="slow task", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="c-3",
                tool_name="jarvis_action",
                tool_args={"request": "slow task"},
            ),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.15)

    result = provider.session.tool_results[0][2]
    assert result["success"] is False
    assert "did not finish" in result["error"]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_confirm_roundtrip():
    brain = FakeBrain(
        replies=("Should I really restart the app?", "Restarted."),
    )
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="starte die app neu",  # i18n-allow: German confirm fixture
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="c-4",
                tool_name="jarvis_action",
                tool_args={"request": "restart the app"},
            ),
            RealtimeEvent(type="turn_complete"),
            RealtimeEvent(
                type="input_transcript",
                text="ja bitte",  # i18n-allow: German confirm fixture
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="c-5",
                tool_name="jarvis_action",
                tool_args={"request": "yes"},
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    replies = [r[2]["spoken_reply"] for r in provider.session.tool_results]
    assert replies == ["Should I really restart the app?", "Restarted."]
    # The confirmation answer went through in the user's own words.
    assert brain.calls[1][0] == "ja bitte"  # i18n-allow: German confirm fixture
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_untranscribed_tool_call_rejected(monkeypatch):
    monkeypatch.setattr("jarvis.realtime.session._TOOL_TRANSCRIPT_WAIT_S", 0.01)
    brain = FakeBrain()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="tool_call",
                call_id="c-6",
                tool_name="jarvis_action",
                tool_args={"request": "mystery action"},
            ),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    assert brain.calls == []
    assert provider.session.tool_results[0][2]["success"] is False
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_tasks_cancelled_on_end():
    gate = asyncio.Event()  # never set
    brain = FakeBrain(gate=gate)
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="long task", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="c-7",
                tool_name="jarvis_action",
                tool_args={"request": "long task"},
            ),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")
    await asyncio.sleep(0.02)

    assert provider.session.tool_results == []
    assert sess._delegate_tasks == set()


@pytest.mark.asyncio
async def test_delegate_brain_exception_sends_safe_failure():
    brain = FakeBrain(error=RuntimeError("boom"))
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="do it", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="c-8",
                tool_name="jarvis_action",
                tool_args={"request": "do it"},
            ),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    result = provider.session.tool_results[0][2]
    assert result["success"] is False
    assert "failed safely" in result["error"]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_degrades_kwargs_but_keeps_voice_confirm():
    """An older Brain receives only its supported confirmation keyword."""

    class LegacyBrain:
        def __init__(self):
            self.calls = []

        async def generate(self, text, *, allow_voice_confirm=False):
            self.calls.append((text, allow_voice_confirm))
            return "done legacy"

        async def __call__(self, text):
            raise AssertionError("bare call must not be reached")

    brain = LegacyBrain()
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="open it", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="c-9",
                tool_name="jarvis_action",
                tool_args={"request": "open it"},
            ),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    assert brain.calls == [("open it", True)]
    assert provider.session.tool_results[0][2]["spoken_reply"] == "done legacy"
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_delegate_does_not_retry_an_internal_type_error():
    """A TypeError after dispatch may follow a side effect and is terminal."""

    class TypeErrorBrain:
        def __init__(self):
            self.calls = 0

        async def generate(self, text, **kwargs):
            del text, kwargs
            self.calls += 1
            raise TypeError("simulated internal failure after dispatch")

        async def __call__(self, text):
            del text
            raise AssertionError("fallback call must not retry the turn")

    brain = TypeErrorBrain()
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="open it", is_final=True),
            RealtimeEvent(
                type="tool_call",
                call_id="type-error-once",
                tool_name="jarvis_action",
                tool_args={"request": "open it"},
            ),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    assert brain.calls == 1
    assert provider.session.tool_results[0][2]["success"] is False
    await sess.end(reason="test")
