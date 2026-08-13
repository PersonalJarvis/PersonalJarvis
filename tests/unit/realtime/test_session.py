import asyncio

import pytest

from jarvis.brain.output_filter import scrub_for_voice
from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.events import (
    AnnouncementRequested,
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
async def test_browser_webrtc_offer_reaches_provider_and_answer_returns_in_ready():
    class AnswerProvider(FakeProvider):
        requires_webrtc_offer = True

        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = FakeSession(self._events)
            self.session.answer_sdp = "v=0\r\no=provider-answer"
            return self.session

    messages = []
    provider = AnswerProvider([])
    sess = RealtimeVoiceSession(
        session_id="s-webrtc-sdp",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control(
        {
            "type": "audio_start",
            "sample_rate": 48_000,
            "webrtc_offer_sdp": "v=0\r\no=browser-offer",
        }
    )

    assert provider.opened_with.transport_offer_sdp == "v=0\r\no=browser-offer"
    ready = next(message for message in messages if message["type"] == "audio_ready")
    assert ready["webrtc_answer_sdp"] == "v=0\r\no=provider-answer"
    assert ready["requires_webrtc_answer"] is True
    await sess.end(reason="test")


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
async def test_total_handshake_failure_emits_audio_failed():
    """When EVERY provider fails, the surfaces get a terminal frame naming
    the last provider and the reason — before it, the desktop status rows
    watched their connecting window expire into idle with nothing shown
    (live 2026-08-08)."""
    messages = []
    sess = RealtimeVoiceSession(
        session_id="all-fail",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        providers=[FailingProvider([])],
        config=_cfg(),
        bus=None,
    )

    try:
        await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    except RuntimeError:
        pass  # the raise stays — only the frame emission is under test
    await sess.end(reason="test")

    failed = next(
        message for message in messages if message.get("type") == "audio_failed"
    )
    assert failed["provider"] == "failing-family"
    assert "simulated depleted credits" in failed["error"]
    assert failed["recoverable"] is True


class UnavailableWithReasonProvider(FakeProvider):
    """Declines and says why — the optional explanation capability."""

    name = "explaining-family"
    duplex_unavailable_reason = (
        "The local voice server is not answering yet — it is starting in the "
        "background. Try the call again in about a minute."
    )

    async def can_open_duplex_session(self):
        return False


class MuteUnavailableProvider(FakeProvider):
    """Declines without explaining — the generic sentence must cover it."""

    name = "mute-family"

    async def can_open_duplex_session(self):
        return False


@pytest.mark.asyncio
async def test_declined_probe_surfaces_the_providers_own_sentence():
    """The handshake summary reaches a user-facing toast verbatim.

    Live 2026-08-09: a cold managed server ended the call with "RuntimeError:
    duplex capability probe reported unavailable" — the mechanism, not the
    situation, and no next step. A provider that phrases its own refusal keeps
    that phrasing whole, exception class name included nowhere.
    """
    messages = []
    sess = RealtimeVoiceSession(
        session_id="explained-decline",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        providers=[UnavailableWithReasonProvider([])],
        config=_cfg(),
        bus=None,
    )

    try:
        await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    except RuntimeError:
        pass  # the raise stays — only the wording is under test
    await sess.end(reason="test")

    failed = next(
        message for message in messages if message.get("type") == "audio_failed"
    )
    assert "starting in the background" in failed["error"]
    assert "Try the call again" in failed["error"]
    assert "RuntimeError" not in failed["error"]
    assert "duplex" not in failed["error"].lower()


@pytest.mark.asyncio
async def test_silent_decline_still_reads_as_a_sentence():
    messages = []
    sess = RealtimeVoiceSession(
        session_id="mute-decline",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        providers=[MuteUnavailableProvider([])],
        config=_cfg(),
        bus=None,
    )

    try:
        await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    except RuntimeError:
        pass
    await sess.end(reason="test")

    failed = next(
        message for message in messages if message.get("type") == "audio_failed"
    )
    assert "no free capacity" in failed["error"]
    assert "RuntimeError" not in failed["error"]


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

    monkeypatch.setattr(session_module, "_AUDIO_SEND_TIMEOUT_S", 0.02)
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
    assert sum(item.get("type") == "tts_cancel" for item in messages) == 1
    assert sum(item.get("type") == "error_spoken" for item in messages) == 1
    assert [item.get("type") for item in messages].index("tts_cancel") < [
        item.get("type") for item in messages
    ].index("error_spoken")


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
    assert {"type": "tts_cancel"} in jsons


@pytest.mark.asyncio
async def test_isolated_dash_delta_keeps_realtime_audio_playing():
    first = b"\x11\x22" * 8
    continuation = b"\x33\x44" * 8
    events = [
        RealtimeEvent(type="output_transcript_delta", text="A safe opening clause"),
        RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=first, sample_rate=24_000, timestamp_ns=0),
        ),
        RealtimeEvent(type="output_transcript_delta", text="\N{EM DASH}"),
        RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(
                pcm=continuation,
                sample_rate=24_000,
                timestamp_ns=0,
            ),
        ),
        RealtimeEvent(
            type="output_transcript_delta",
            text="followed by a safe continuation.",
        ),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries: list[bytes] = []
    jsons: list[dict] = []
    sess = RealtimeVoiceSession(
        session_id="streaming-dash",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert binaries == [first, continuation]
    assert not any(item.get("type") == "tts_cancel" for item in jsons)
    assert not any(item.get("type") == "error_spoken" for item in jsons)


@pytest.mark.asyncio
async def test_split_filler_opener_keeps_realtime_answer_playing():
    answer_audio = b"\x55\x66" * 8
    events = [
        RealtimeEvent(type="output_transcript_delta", text="Let me"),
        RealtimeEvent(type="output_transcript_delta", text=" think"),
        RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(
                pcm=answer_audio,
                sample_rate=24_000,
                timestamp_ns=0,
            ),
        ),
        RealtimeEvent(
            type="output_transcript_delta",
            text=", the benefits include stronger bones.",
        ),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries: list[bytes] = []
    jsons: list[dict] = []
    sess = RealtimeVoiceSession(
        session_id="streaming-filler-opener",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert binaries == [answer_audio]
    assert not any(item.get("type") == "tts_cancel" for item in jsons)
    assert not any(item.get("type") == "error_spoken" for item in jsons)


@pytest.mark.asyncio
async def test_audio_after_a_leak_transcript_is_never_emitted():
    # The gate flows audio unconditionally once the turn opening is vetted
    # clean (maintainer mandate 2026-07-18, BUG-080 follow-up: zero
    # gate-caused mid-reply interruptions). A later segment's audio (a2) is
    # therefore audible BEFORE its own transcript is scrubbed — accepted
    # trade-off. The trailing kill switch is the remaining guarantee: from
    # the moment the leaking transcript is detected, the response cancels
    # and no further provider audio (a3) may reach the user.
    a1 = b"\x11\x22" * 8
    a2 = b"\x33\x44" * 36_000
    a3 = b"\x55\x66" * 8
    events = [
        RealtimeEvent(
            type="audio_delta", audio=AudioChunk(pcm=a1, sample_rate=24000, timestamp_ns=0)
        ),
        RealtimeEvent(
            type="output_transcript_delta",
            text="This is the normal English opening for the user.",
        ),
        RealtimeEvent(
            type="audio_delta", audio=AudioChunk(pcm=a2, sample_rate=24000, timestamp_ns=0)
        ),
        RealtimeEvent(
            type="output_transcript_delta",
            text="Traceback (most recent call last):\n  File x\nValueError: y\n\n",
        ),
        RealtimeEvent(
            type="audio_delta", audio=AudioChunk(pcm=a3, sample_rate=24000, timestamp_ns=0)
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
    assert a2 in binaries  # flowed before its transcript — mandated trade-off
    assert a3 not in binaries  # after the leak: kill switch, nothing plays


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
    # VoiceSessionStarted stays pipeline-owned on desktop; the session END is
    # published from every surface so the wiki completeness sweep never
    # depends on the outer layer alone. The desktop pipeline publishes a
    # second end event with the same session_id; sweep consumers treat the
    # duplicate as a no-op (their per-session buffer is popped by the first).
    assert "VoiceSessionStarted" not in by_name
    ended = by_name["VoiceSessionEnded"]
    assert ended.session_id == "desktop-telemetry"


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
    assert [item.turn_index for item in started] == [0, 1]
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
    history = "\n".join(str(message.content) for message in sess._delegate_history)
    assert "Trusted Jarvis-Agent mission result" in history
    assert "artifact: report.md" in history


@pytest.mark.asyncio
async def test_user_speech_during_silent_external_update_reclaims_the_turn():
    """BUG-103: real user input during a still-silent readback owns the turn.

    Live forensic 2026-07-20 17:50: a late action-result injection raced the
    user's next utterance. The turn kept its readback state, so it completed
    on the external-update track — the user's answer (already spoken by the
    surface TTS fallback) was re-published as a second spoken event and the
    turn produced no ResponseGenerated/VoiceTurnCompleted record at all,
    which the transcript view rendered as the same reply twice.
    """
    provider = TextResultGatedProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="What can I do?",
                is_final=True,
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text="Here is what you can do.",
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    bus = FakeBus()
    sess = RealtimeVoiceSession(
        session_id="reclaimed-update",
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
    )
    await sess.wait_finished()
    await sess.end(reason="test")

    assert accepted is True
    # The unrendered readback was dropped; nothing may claim it was spoken,
    # and the user's answer must not surface twice under the readback kind.
    assert not any(
        isinstance(event, SpeechSpoken) and event.spoken_kind == "subagent"
        for event in bus.events
    )
    # The turn belongs to the user: full user-turn record chain.
    assert any(isinstance(event, VoiceTurnStarted) for event in bus.events)
    responses = [
        event for event in bus.events if isinstance(event, ResponseGenerated)
    ]
    assert [event.text for event in responses] == ["Here is what you can do."]
    completed = [
        event for event in bus.events if isinstance(event, VoiceTurnCompleted)
    ]
    assert len(completed) == 1
    assert completed[0].user_text == "What can I do?"
    assert completed[0].jarvis_text == "Here is what you can do."


@pytest.mark.asyncio
async def test_hijacked_external_update_turn_completes_on_the_user_track():
    """BUG-103 belt-and-braces: a delegate turn inside a readback turn wins.

    Even when the abort-on-user-speech path is bypassed (e.g. the readback had
    already started rendering when the user barged in and a delegate answered
    the new utterance), turn completion must publish the user track exactly
    once instead of re-publishing the answer under the readback kind.
    """
    from jarvis.realtime.session import (
        _DelegateTurnState,
        _ExternalUpdateState,
    )

    bus = FakeBus()
    sess = RealtimeVoiceSession(
        session_id="hijacked-update",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(),
        bus=bus,
        surface="desktop",
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    sess._turn_id = "hijacked-turn"
    sess._last_user_text = "What can I do?"
    sess._output_transcript = ["Here is what you can do."]
    sess._output_samples_sent = 0
    sess._delegate_turns["hijacked-turn"] = _DelegateTurnState(
        deterministic=True,
        result_complete=True,
        result_success=True,
        delivery_started=True,
        last_reply="Here is what you can do.",
        user_text="What can I do?",
    )
    sess._external_update = _ExternalUpdateState(
        source_text="Old late action result.",
        language="en",
        spoken_kind="action_result",
    )

    await sess._publish_turn_completed()
    await sess.end(reason="test")

    assert not any(
        isinstance(event, SpeechSpoken)
        and event.spoken_kind == "action_result"
        for event in bus.events
    )
    responses = [
        event for event in bus.events if isinstance(event, ResponseGenerated)
    ]
    assert [event.text for event in responses] == ["Here is what you can do."]
    completed = [
        event for event in bus.events if isinstance(event, VoiceTurnCompleted)
    ]
    assert len(completed) == 1
    assert completed[0].user_text == "What can I do?"
    assert sess._external_update is None


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
    history = "\n".join(str(message.content) for message in sess._delegate_history)
    assert "The mission finished." in history
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
async def test_ordinary_speech_does_not_hang_up(monkeypatch):
    monkeypatch.setattr(
        runtime_refs,
        "get_supervisor_tool_gateway",
        lambda: None,
    )

    class _WaitForSafeGroundingFallbackSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="wie ist das wetter heute",  # i18n-allow: ordinary speech guard
                is_final=True,
            )
            for _ in range(100):
                if self.text_inputs:
                    break
                await asyncio.sleep(0.01)
            yield RealtimeEvent(type="turn_complete")

    class _WaitForSafeGroundingFallbackProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _WaitForSafeGroundingFallbackSession([])
            return self.session

    provider = _WaitForSafeGroundingFallbackProvider([])
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
    assert provider.session.response_requests == 0
    assert provider.session.text_inputs
    assert "couldn't verify that reliably" in provider.session.text_inputs[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "user_text", "wrong_text", "correct_text"),
    [
        (
            "en",
            "What is kindness?",
            "Xin chào, đây là câu trả lời bằng tiếng Việt với nhiều từ rõ "
            "ràng để xác định ngôn ngữ.",
            "Kindness is the practice of treating other people with care "
            "and respect.",
        ),
        (
            "de",
            "Was ist Freundlichkeit?",  # i18n-allow
            "这是一个完整的中文回答，包含足够多的文字来可靠地识别语言。",
            "Freundlichkeit bedeutet, andere Menschen mit Fürsorge und "  # i18n-allow
            "Respekt zu behandeln.",  # i18n-allow
        ),
    ],
)
async def test_wrong_output_language_retries_once_before_releasing_pcm(
    language: str,
    user_text: str,
    wrong_text: str,
    correct_text: str,
):
    wrong_pcm = b"\x02\x03" * 32
    correct_pcm = b"\x10\x20" * 32

    class _LanguageRetrySession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text=user_text,
                is_final=True,
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=wrong_pcm,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            yield RealtimeEvent(
                type="output_transcript_delta",
                text=wrong_text,
            )
            for _ in range(100):
                if self.response_requests >= 2:
                    break
                await asyncio.sleep(0.01)
            yield RealtimeEvent(
                type="output_transcript_delta",
                text=correct_text,
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=correct_pcm,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            yield RealtimeEvent(type="turn_complete")

    class _LanguageRetryProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _LanguageRetrySession([])
            return self.session

    provider = _LanguageRetryProvider([])
    jsons: list[dict] = []
    binaries: list[bytes] = []
    sess = RealtimeVoiceSession(
        session_id=f"language-retry-{language}",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language=language),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert provider.session.response_requests == 2
    assert provider.session.interrupts == 1
    assert binaries == [correct_pcm]
    assert [
        item["text"]
        for item in jsons
        if item.get("type") == "transcript"
        and item.get("role") == "assistant"
    ] == [correct_text]
    assert sess._output_language_mismatches == 1  # noqa: SLF001
    assert sess._output_language_retries == 1  # noqa: SLF001
    assert sess._output_language_failures == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_prompted_retry_capability_routes_language_retry_via_send_text():
    """A transport that keeps the cancelled answer in its conversation (the
    self-hosted card) advertises ``supports_prompted_response_retry``; the one
    language retry must then arrive as an explicit ``send_text`` request, not
    a bare ``response.create`` against a history that already contains the
    blocked answer (live 2026-08-10: that regenerated as one empty token and
    the call went silent)."""
    user_text = "What is kindness?"
    wrong_text = (
        "这是一个完整的中文回答，包含足够多的文字来可靠地识别语言。"
    )
    correct_text = (
        "Kindness is the practice of treating other people with care "
        "and respect."
    )
    wrong_pcm = b"\x02\x03" * 32
    correct_pcm = b"\x10\x20" * 32

    class _PromptedRetrySession(FakeSession):
        supports_prompted_response_retry = True

        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text=user_text,
                is_final=True,
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=wrong_pcm,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            yield RealtimeEvent(
                type="output_transcript_delta",
                text=wrong_text,
            )
            for _ in range(100):
                if self.text_inputs:
                    break
                await asyncio.sleep(0.01)
            yield RealtimeEvent(
                type="output_transcript_delta",
                text=correct_text,
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=correct_pcm,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            yield RealtimeEvent(type="turn_complete")

    class _PromptedRetryProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _PromptedRetrySession([])
            return self.session

    provider = _PromptedRetryProvider([])
    jsons: list[dict] = []
    binaries: list[bytes] = []
    sess = RealtimeVoiceSession(
        session_id="language-retry-prompted",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language="en"),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    # The original turn is the only bare response request; the retry itself
    # travels as one explicit prompted request in the pinned language.
    assert provider.session.response_requests == 1
    assert len(provider.session.text_inputs) == 1
    assert "English" in provider.session.text_inputs[0]
    assert binaries == [correct_pcm]
    assert sess._output_language_retries == 1  # noqa: SLF001
    assert sess._output_language_failures == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_session_voice_renders_the_fallback_when_it_owns_the_only_tts():
    """A transport whose voice exists only behind the live session (the
    self-hosted card) opts into ``renders_surface_fallback``: after the one
    language retry also fails, the safety-net phrase must ride the session
    itself instead of the surface's ``error_spoken`` path — the surface has
    no realtime-scoped TTS there and kept the whole turn silent (live
    2026-08-10 17:04/17:08)."""
    user_text = "What is kindness?"
    wrong_text = (
        "这是一个完整的中文回答，包含足够多的文字来可靠地识别语言。"
    )
    wrong_pcm = b"\x02\x03" * 32

    class _SelfRenderingSession(FakeSession):
        supports_prompted_response_retry = True
        renders_surface_fallback = True

        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text=user_text,
                is_final=True,
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=wrong_pcm,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            yield RealtimeEvent(
                type="output_transcript_delta",
                text=wrong_text,
            )
            for _ in range(100):
                if self.text_inputs:
                    break
                await asyncio.sleep(0.01)
            # The prompted retry ALSO comes back in the wrong language, so
            # the session must fall through to the safety-net phrase.
            yield RealtimeEvent(
                type="output_transcript_delta",
                text=wrong_text,
            )
            for _ in range(100):
                if len(self.text_inputs) >= 2:
                    break
                await asyncio.sleep(0.01)
            yield RealtimeEvent(type="turn_complete")

    class _SelfRenderingProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _SelfRenderingSession([])
            return self.session

    provider = _SelfRenderingProvider([])
    jsons: list[dict] = []
    binaries: list[bytes] = []
    sess = RealtimeVoiceSession(
        session_id="fallback-session-voice",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language="en"),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    # Retry first, then the session-voice fallback render — and no
    # error_spoken detour to a surface TTS that does not exist there.
    assert len(provider.session.text_inputs) == 2
    assert "English" in provider.session.text_inputs[0]
    failure_phrase = sess._output_language_failure_phrase("en")  # noqa: SLF001
    assert failure_phrase in provider.session.text_inputs[1]
    assert [item for item in jsons if item.get("type") == "error_spoken"] == []
    assert sess._output_language_failures == 1  # noqa: SLF001


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



def _spoke_surface_line(jsons, text, language="en"):
    """Whether the surface was asked to speak exactly this line.

    Field-wise on purpose: ``error_spoken`` is a growing message contract (it
    now also names the realtime provider so the desktop surface can resolve a
    realtime-scoped TTS for it), and whole-dict equality makes every future
    addition look like a regression in tests that only care about the words.
    """
    return any(
        item.get("type") == "error_spoken"
        and item.get("text") == text
        and item.get("language") == language
        for item in jsons
    )


def _delegate_cfg(tool_mode="delegate"):
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
    tool_mode="delegate",
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
async def test_local_public_fact_uses_exactly_one_supervisor_search(
    wire_supervisor_gateway,
):
    class _SearchTool(_StubTool):
        name = "search_web"
        description = "Search public web sources."
        risk_tier = "safe"

    class _RecordingSearchExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool, arguments, **kwargs):
            self.calls.append((tool, arguments, kwargs))
            return ToolResult(
                success=True,
                output={
                    "status": "ok",
                    "results": [
                        {
                            "title": "Public source",
                            "url": "https://example.test/source",
                            "snippet": "Ada Lovelace was born in 1815.",
                        }
                    ],
                },
            )

    class _GroundingBrain(FakeBrain):
        def __init__(self):
            super().__init__()
            self.run_task_calls = []

        async def run_task(self, **kwargs):
            self.run_task_calls.append(kwargs)
            return "Ada Lovelace was born in 1815 according to the source."

    class _GroundedResultSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="When was Ada Lovelace born?",
                is_final=True,
            )
            for _ in range(100):
                if self.text_inputs:
                    break
                await asyncio.sleep(0.01)
            yield RealtimeEvent(type="turn_complete")

    class _LocalFactProvider(FakeProvider):
        requires_public_fact_grounding = True

        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _GroundedResultSession([])
            return self.session

    brain = _GroundingBrain()
    brain._tools = {"search_web": _SearchTool()}
    executor = _RecordingSearchExecutor()
    wire_supervisor_gateway(brain, executor)
    provider = _LocalFactProvider([])
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert len(executor.calls) == 1
    assert executor.calls[0][1] == {
        "query": "When was Ada Lovelace born?",
        "max_results": 5,
    }
    assert len(brain.run_task_calls) == 1
    assert brain.run_task_calls[0]["allowed_tools"] == ()
    assert brain.calls == []
    assert len(provider.session.text_inputs) == 1
    assert "Ada Lovelace was born" in provider.session.text_inputs[0]
    assert "according to the source" in provider.session.text_inputs[0]


@pytest.mark.asyncio
async def test_direct_mode_uses_delegate_when_provider_cannot_declare_tools():
    """A capability-limited subscription transport must keep actions usable."""

    class NoDirectToolsProvider(FakeProvider):
        supports_direct_tools = False

    provider = NoDirectToolsProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=FakeBrain(), tool_mode="direct")

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert _tool_names(provider.opened_with) == ["jarvis_action", "end_call"]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_capability_forced_delegate_executes_provider_handoff(caplog):
    caplog.set_level("INFO")

    class NoDirectToolsProvider(FakeProvider):
        supports_direct_tools = False

    brain = FakeBrain(replies=("The settings are open.",))
    provider = NoDirectToolsProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Open the settings view.",
                is_final=True,
            ),
            RealtimeEvent(
                type="handoff_requested",
                text="Open the settings view.",
                handoff_id="handoff-1",
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    sess = _session(
        provider,
        brain=brain,
        tool_mode="direct",
        jsons=jsons,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await _wait_until(lambda: bool(provider.session.text_inputs))

    assert brain.calls[0][0] == "Open the settings view."
    assert not [item for item in jsons if item.get("type") == "provider_error"]
    await sess.end(reason="test")
    assert sess._handoff_action_turns == 1
    assert sess._handoff_requests == 1
    assert sess._handoff_delegate_dispatches == 1
    assert sess._handoff_declines == 0
    assert "handoff_obligation_misses=0" in caplog.text


@pytest.mark.asyncio
async def test_handoff_without_a_delegate_declines_instead_of_ending_the_call():
    """A missing executor must cost the ACTION, never the conversation.

    A transport that cannot declare tools natively (the ChatGPT-subscription
    voice) reaches every action through ``handoff_requested``. When no
    deterministic delegate is available the session used to fail terminally —
    it hung up mid-sentence because one action could not be routed. The honest
    behaviour is to say so out loud and keep the call.
    """

    class NoDirectToolsProvider(FakeProvider):
        supports_direct_tools = False

    provider = NoDirectToolsProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Open the settings view.",
                is_final=True,
            ),
            RealtimeEvent(
                type="handoff_requested",
                text="Open the settings view.",
                handoff_id="handoff-no-delegate",
            ),
            RealtimeEvent(type="output_transcript_delta", text="Still here."),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    # No brain => no deterministic delegate, and this transport cannot receive
    # tool declarations either, so the handoff has nowhere to go.
    sess = _session(provider, brain=None, tool_mode="delegate", jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert not [item for item in jsons if item.get("type") == "provider_error"]
    spoken = [item for item in jsons if item.get("type") == "error_spoken"]
    assert spoken, "the declined handoff must be voiced, not swallowed"
    assert "action" in spoken[0]["text"].lower()
    # The stream kept being consumed after the refusal.
    assert any(item.get("type") == "turn_complete" for item in jsons)
    await sess.end(reason="test")
    assert sess._handoff_action_turns == 1
    assert sess._handoff_requests == 1
    assert sess._handoff_delegate_dispatches == 0
    assert sess._handoff_declines == 1


@pytest.mark.asyncio
async def test_provider_eof_waits_for_supervised_delegate_delivery():
    """A finite provider stream must not abandon its accepted handoff."""

    class NoDirectToolsProvider(FakeProvider):
        supports_direct_tools = False

    gate = asyncio.Event()
    brain = FakeBrain(replies=("The settings are open.",), gate=gate)
    provider = NoDirectToolsProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Open the settings view.",
                is_final=True,
            ),
            RealtimeEvent(
                type="handoff_requested",
                text="Open the settings view.",
                handoff_id="handoff-eof",
            ),
        ]
    )
    jsons: list[dict] = []
    sess = _session(provider, brain=brain, tool_mode="direct", jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(lambda: bool(brain.calls))
    finished = asyncio.create_task(sess.wait_finished())
    await asyncio.sleep(0)
    assert not finished.done()

    gate.set()
    await asyncio.wait_for(finished, timeout=2.0)

    surfaced = [item for item in jsons if item.get("type") == "error_spoken"]
    assert [item["text"] for item in surfaced] == ["The settings are open."]
    assert len([item for item in jsons if item.get("type") == "turn_complete"]) == 1
    assert not [item for item in jsons if item.get("type") == "provider_error"]
    await sess.end(reason="test")
    assert brain.cancelled is False


@pytest.mark.asyncio
async def test_delegate_directive_names_screen_control_and_forbids_capability_denial():
    """The live model must know its on-screen reach and never deny it.

    Live forensic 2026-07-15 07:59: asked why a screen action failed, the
    model claimed it had no API access and offered to type via 'a script or
    the keyboard' — inventing capability gaps instead of calling
    jarvis_action. The directive must name Computer-Use-style screen control
    explicitly and forbid claiming a missing tool/API/access for anything in
    the user's world.
    """
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=FakeBrain())

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    instructions = provider.opened_with.instructions
    assert "clicks, types, and navigates" in instructions
    assert "Never tell the user that you lack" in instructions
    declaration = next(
        d for d in provider.opened_with.tools if d["name"] == "jarvis_action"
    )
    assert "click, type, and navigate" in declaration["description"]


def test_delegate_history_keeps_a_task_five_exchanges_back():
    """The window must survive a correction sequence plus announcements.

    Live forensic 2026-07-15 08:00: after four correction turns and two
    background-completion notes, the original announce request had just been
    trimmed out of the 8-message window — the final mission posted a
    placeholder announcement instead of the requested content.
    """
    sess = _session(FakeProvider([]), brain=FakeBrain())
    sess._remember_delegate_turn(
        "Announce the live event on my Personal Jarvis server.", "On it."
    )
    # One failure completion + four correction exchanges follow, mirroring
    # the live session's shape.
    sess._remember_delegate_turn("", "[Trusted background completion]\nIt failed.")
    for index in range(4):
        sess._remember_delegate_turn(f"correction {index}", f"reply {index}")
    sess._remember_delegate_turn("", "[Trusted background completion]\nDone-ish.")

    contents = [str(m.content) for m in sess._delegate_history]
    assert any("Personal Jarvis" in c for c in contents), (
        f"the original task must survive the correction sequence: {contents}"
    )


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
        (
            "Ach, ich versuche gerade Suggestionen zu studieren. "  # i18n-allow
            "Wie würdest du mir am besten dabei helfen, "  # i18n-allow
            "Suggestionen anzuwenden und konkreter zu benutzen, "  # i18n-allow
            "um meine Mitmenschen dazu zu bringen, "  # i18n-allow
            "meine Interessen zu verfolgen?"  # i18n-allow
        ),
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


@pytest.mark.asyncio
async def test_native_turn_update_discourages_delegation():
    """A planner-NATIVE turn steers the model AWAY from the action function.

    The planner verdict used to work in one direction only (forcing
    delegation); a NATIVE verdict changed nothing, so a delegation-biased
    provider still round-tripped plain world knowledge through the router
    brain (live incident 2026-07-16 11:23, 16 s of web searches for a
    net-worth question). The tool stays declared; the directive flips.
    """
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="How much money does Peter Thiel have?",
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=FakeBrain())

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    update = provider.session.session_updates[-1]["instructions"]
    assert "Answer it directly from your own knowledge" in update
    assert "orchestrator is handling this current turn" not in update
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_forced_turn_update_never_carries_discourage_directive():
    """The orchestrator-owned branch wins over the discourage branch."""
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="What is in my Gmail inbox?",
                is_final=True,
            ),
        ]
    )
    sess = _session(provider, brain=FakeBrain())

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    update = provider.session.session_updates[-1]["instructions"]
    assert "orchestrator is handling this current turn" in update
    assert "Answer it directly from your own knowledge" not in update
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_scrub_trip_during_delegate_readback_speaks_trusted_reply():
    """A tripped transcript hold re-speaks the delivered reply, not an error.

    Gemini renders an injected trusted result faster than real time while its
    output transcription lags entirely (live incident 2026-07-16 11:24). With
    no transcript delta the whole turn, the gate fails closed at
    turn_complete (BUG-069: the mid-turn buffer cap no longer trips first) —
    and that path used to speak a generic error AFTER the user waited through
    the whole delegated action. The reply text is our own already-delivered
    brain output, so the surface TTS must speak it instead.
    """
    reply = "The delegated answer the user must still hear."
    # 3 s of 24 kHz 16-bit PCM per chunk — audio arrives, its transcript
    # never does, and the turn ends normally.
    three_seconds = AudioChunk(
        pcm=b"\x01\x02" * 72_000, sample_rate=24_000, timestamp_ns=0
    )

    class _GatedReadbackSession(FakeSession):
        def __init__(self, events):
            super().__init__(events)
            self._text_sent = asyncio.Event()

        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="What is in my Gmail inbox?",
                is_final=True,
            )
            await self._text_sent.wait()
            async for event in super().receive():
                yield event

        async def send_text(self, text):
            await super().send_text(text)
            self._text_sent.set()

    class _GatedReadbackProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _GatedReadbackSession(self._events)
            return self.session

    jsons: list[dict] = []
    binaries: list[bytes] = []
    provider = _GatedReadbackProvider(
        [
            RealtimeEvent(type="audio_delta", audio=three_seconds),
            RealtimeEvent(type="audio_delta", audio=three_seconds),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(
        provider,
        brain=FakeBrain(replies=(reply,)),
        jsons=jsons,
        binaries=binaries,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=5)
    await sess.end(reason="test")

    assert provider.session.text_inputs, "trusted result was never injected"
    spoken = [item for item in jsons if item.get("type") == "error_spoken"]
    assert [item["text"] for item in spoken] == [reply]
    assert not any("error occurred" in item["text"].lower() for item in spoken)
    assert binaries == []


class _GenerativeVoiceSession(FakeSession):
    """A provider session whose native audio renderer is generative (BUG-086:
    Gemini Live's audio can drift on readbacks). The former
    ``renders_pinned_voice = False`` escalation flag is gone: the surface-TTS
    claim it triggered spoke every delegate reply in an audibly different
    voice and was reverted (maintainer live verdict 2026-07-21)."""

    def __init__(self, events):
        super().__init__(events)
        self.release = asyncio.Event()
        self._text_sent = asyncio.Event()

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="What is in my Gmail inbox?",
            is_final=True,
        )
        await self._text_sent.wait()
        for event in self._events:
            yield event
            await asyncio.sleep(0)
        # Keep the duplex stream open so the test controls session teardown.
        await self.release.wait()

    async def send_text(self, text):
        await super().send_text(text)
        self._text_sent.set()


class _GenerativeVoiceProvider(FakeProvider):
    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = _GenerativeVoiceSession(self._events)
        return self.session


@pytest.mark.asyncio
async def test_generative_voice_provider_delegate_reply_renders_natively_on_desktop():
    """One voice = the NATIVE realtime voice (BUG-086 escalation reverted):
    when the provider renders the injected trusted result inside the
    readback window, the desktop surface must NOT speak it through the
    surface TTS — the flash-TTS rendering of the pinned voice is audibly a
    different voice than the live model's, so an immediate surface claim
    was a guaranteed voice flip on every tool-model turn (maintainer live
    verdict 2026-07-21).
    """
    reply = "The deep answer that must keep the native voice."
    spoken_audio = AudioChunk(
        pcm=b"\x01\x02" * 8, sample_rate=24_000, timestamp_ns=0
    )
    jsons: list[dict] = []
    binaries: list[bytes] = []
    provider = _GenerativeVoiceProvider(
        [
            RealtimeEvent(
                type="output_transcript_delta", text=reply, is_final=True
            ),
            RealtimeEvent(type="audio_delta", audio=spoken_audio),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="generative-voice-native-readback",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_delegate_cfg("delegate"),
        brain=FakeBrain(replies=(reply,)),
        surface="desktop",
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    async def _played():
        while not binaries:  # noqa: ASYNC110 - callback exposes no wait handle
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_played(), timeout=5)
    # Give a wrong surface rendering time to appear before asserting.
    await asyncio.sleep(0.3)
    assert binaries == [spoken_audio.pcm]
    assert not any(m.get("type") == "error_spoken" for m in jsons)
    # The provider received the trusted result to render.
    assert provider.session.text_inputs, "trusted result was never injected"
    provider.session.release.set()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_generative_voice_provider_mute_still_falls_back_to_surface_tts():
    """The revert must not lose the mute net: a provider that never renders
    the injected result still gets the reply spoken via the surface TTS
    after the readback wait window."""
    reply = "The answer a mute provider never rendered."
    jsons: list[dict] = []
    binaries: list[bytes] = []
    provider = _GenerativeVoiceProvider([])
    sess = RealtimeVoiceSession(
        session_id="generative-voice-mute-fallback",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_delegate_cfg("delegate"),
        brain=FakeBrain(replies=(reply,)),
        surface="desktop",
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    async def _spoken():
        while not any(  # noqa: ASYNC110 - callback exposes no wait handle
            m.get("type") == "error_spoken" for m in jsons
        ):
            await asyncio.sleep(0.01)

    # Must take at least the readback wait window (2.5 s) — an immediate
    # surface claim would be the reverted escalation sneaking back in.
    start = asyncio.get_event_loop().time()
    await asyncio.wait_for(_spoken(), timeout=8)
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 2.0, "surface TTS claimed the reply before the wait window"
    spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    assert [m["text"] for m in spoken] == [scrub_for_voice(reply).cleaned]
    assert binaries == []
    provider.session.release.set()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_generative_voice_provider_keeps_native_readback_on_the_browser_surface():
    """The browser surface has no server-side TTS for error_spoken, so a
    generative-voice provider still reads the delegate reply back natively
    there — silence would be worse than a possible voice drift.
    """
    reply = "The browser hears the native readback."
    spoken_audio = AudioChunk(
        pcm=b"\x01\x02" * 8, sample_rate=24_000, timestamp_ns=0
    )
    jsons: list[dict] = []
    binaries: list[bytes] = []
    provider = _GenerativeVoiceProvider(
        [
            RealtimeEvent(
                type="output_transcript_delta", text=reply, is_final=True
            ),
            RealtimeEvent(type="audio_delta", audio=spoken_audio),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(
        provider,
        brain=FakeBrain(replies=(reply,)),
        jsons=jsons,
        binaries=binaries,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    async def _played():
        while not binaries:  # noqa: ASYNC110 - callback exposes no wait handle
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_played(), timeout=5)
    assert binaries == [spoken_audio.pcm]
    assert not any(m.get("type") == "error_spoken" for m in jsons)
    provider.session.release.set()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_generative_voice_readback_race_never_speaks_twice():
    """Pin the one-rendering invariant: when the provider starts a native
    readback around the same instant the surface fallback takes the reply,
    exactly ONE rendering reaches the user — the surface error_spoken or
    the native audio, never both. The claim's flag-set has no await between
    check and set, and _emit_audio re-checks the withhold flags right
    before send; a refactor inserting an await there would break this test.
    """
    reply = "One answer, one voice."
    spoken_audio = AudioChunk(
        pcm=b"\x01\x02" * 8, sample_rate=24_000, timestamp_ns=0
    )
    jsons: list[dict] = []
    binaries: list[bytes] = []
    provider = _GenerativeVoiceProvider(
        [
            RealtimeEvent(
                type="output_transcript_delta", text=reply, is_final=True
            ),
            RealtimeEvent(type="audio_delta", audio=spoken_audio),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="generative-voice-readback-race",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_delegate_cfg("delegate"),
        brain=FakeBrain(replies=(reply,)),
        surface="desktop",
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    async def _one_rendering():
        while not binaries and not any(  # noqa: ASYNC110 - two callback outcomes
            m.get("type") == "error_spoken" for m in jsons
        ):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_one_rendering(), timeout=5)
    # Give a wrong second rendering time to surface before asserting.
    await asyncio.sleep(0.3)
    surface_spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    native_played = bool(binaries)
    assert (len(surface_spoken) == 1) != native_played
    if surface_spoken:
        assert surface_spoken[0]["text"] == reply
    provider.session.release.set()
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


class _TwoTurnClarifySession(FakeSession):
    """Turn 1 delegates and asks a question; Turn 2 is the user's answer."""

    def __init__(self, second_turn_text, spoken_reply="Which trip do you mean?"):
        super().__init__([])
        self._second_turn_text = second_turn_text
        self._spoken_reply = spoken_reply
        self._text_sent = asyncio.Event()

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="Write the travel plan to my wiki.",
            is_final=True,
        )
        await self._text_sent.wait()
        yield RealtimeEvent(
            type="output_transcript_delta",
            text=self._spoken_reply,
            is_final=True,
        )
        await asyncio.sleep(0)
        yield RealtimeEvent(type="turn_complete")
        await asyncio.sleep(0)
        yield RealtimeEvent(
            type="input_transcript",
            text=self._second_turn_text,
            is_final=True,
        )
        await asyncio.sleep(0.1)

    async def send_text(self, text):
        await super().send_text(text)
        self._text_sent.set()


class _TwoTurnClarifyProvider(FakeProvider):
    def __init__(self, second_turn_text, spoken_reply="Which trip do you mean?"):
        super().__init__([])
        self._second_turn_text = second_turn_text
        self._spoken_reply = spoken_reply

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = _TwoTurnClarifySession(
            self._second_turn_text, self._spoken_reply
        )
        return self.session


@pytest.mark.asyncio
async def test_short_answer_to_delegate_clarify_question_is_delegated():
    brain = FakeBrain(
        replies=("Which trip do you mean?", "Saved the San Francisco trip.")
    )
    provider = _TwoTurnClarifyProvider("The one to San Francisco")
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.12)

    assert [call[0] for call in brain.calls] == [
        "Write the travel plan to my wiki.",
        "The one to San Francisco",
    ]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_long_follow_up_after_clarify_question_stays_native():
    brain = FakeBrain(replies=("Which trip do you mean?",))
    provider = _TwoTurnClarifyProvider(
        "Actually tell me a story about a dragon and a knight instead"
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.12)

    assert [call[0] for call in brain.calls] == [
        "Write the travel plan to my wiki.",
    ]
    assert provider.session.required_tools == [None]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_short_follow_up_without_open_question_stays_native():
    brain = FakeBrain(replies=("The travel plan was saved.",))
    provider = _TwoTurnClarifyProvider(
        "Thanks a lot", spoken_reply="The travel plan was saved."
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.12)

    assert [call[0] for call in brain.calls] == [
        "Write the travel plan to my wiki.",
    ]
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
async def test_end_mid_delegate_delivers_exactly_once_without_rerunning_action():
    """Socket teardown must not cancel, lose, or repeat an executing action."""
    dispatch_started = asyncio.Event()
    release_action = asyncio.Event()

    class _BlockingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatch_started.set()
            return await super().generate(text, **kwargs)

    class _OpenUntilEndedSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Write the status to my wiki.",
                is_final=True,
            )
            await asyncio.Event().wait()

    class _OpenUntilEndedProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _OpenUntilEndedSession([])
            return self.session

    brain = _BlockingBrain(
        replies=("The requested wiki update completed successfully.",),
        gate=release_action,
    )
    bus = FakeBus()
    provider = _OpenUntilEndedProvider([])
    sess = _session(provider, brain=brain, bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(dispatch_started.wait(), timeout=2)
    turn_id, turn_state = next(iter(sess._delegate_turns.items()))  # noqa: SLF001

    await asyncio.wait_for(sess.end(reason="test"), timeout=5)
    assert brain.cancelled is False

    release_action.set()
    for _ in range(100):
        announcements = [
            event
            for event in bus.events
            if isinstance(event, AnnouncementRequested)
            and event.kind == "completion"
        ]
        if announcements:
            break
        await asyncio.sleep(0.01)

    assert len(brain.calls) == 1
    assert len(announcements) == 1
    assert announcements[0].text == (
        "The requested wiki update completed successfully."
    )

    # A late duplicate callback may observe the same completed state, but the
    # stable delivery id owns the claim and cannot publish a second result.
    assert not await sess._deliver_detached_delegate_result(  # noqa: SLF001
        turn_id,
        turn_state,
    )
    assert len(
        [
            event
            for event in bus.events
            if isinstance(event, AnnouncementRequested)
            and event.kind == "completion"
        ]
    ) == 1


@pytest.mark.asyncio
async def test_failed_surface_claim_recovers_once_in_originating_language():
    from jarvis.realtime.session import _DelegateTurnState

    async def _failed_surface_send(_message):
        raise RuntimeError("surface disconnected")

    bus = FakeBus()
    binaries = []
    sess = RealtimeVoiceSession(
        session_id="surface-recovery",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=_failed_surface_send,
        provider=FakeProvider([]),
        config=_cfg(reply_language="en"),
        bus=bus,
    )
    state = _DelegateTurnState(
        last_reply="Das Ergebnis wurde erfolgreich gespeichert.",  # i18n-allow
        result_complete=True,
        result_success=True,
        language="de",
        delivery_id="surface-recovery:turn-1",
    )

    assert not await sess._send_delegate_surface_fallback(  # noqa: SLF001
        state,
        state.last_reply,
    )
    assert state.surface_fallback_confirmed is False
    assert state.delivery_completed is True
    assert state.delivery_channel == "detached"

    assert not await sess._deliver_detached_delegate_result(  # noqa: SLF001
        "turn-1",
        state,
    )
    announcements = [
        event for event in bus.events if isinstance(event, AnnouncementRequested)
    ]
    assert len(announcements) == 1
    assert announcements[0].language == "de"
    assert announcements[0].text == state.last_reply
    sess._turn_id = "turn-1"  # noqa: SLF001
    sess._delegate_turns["turn-1"] = state  # noqa: SLF001
    await sess._emit_audio(  # noqa: SLF001
        AudioChunk(
            pcm=b"\x10\x00" * 160,
            sample_rate=24_000,
            timestamp_ns=0,
        )
    )
    assert binaries == []


@pytest.mark.asyncio
async def test_provider_audio_is_blocked_as_soon_as_teardown_begins():
    binaries = []
    sess = _session(FakeProvider([]), binaries=binaries)
    sess._ended = True  # noqa: SLF001

    await sess._emit_audio(  # noqa: SLF001
        AudioChunk(
            pcm=b"\x10\x00" * 160,
            sample_rate=24_000,
            timestamp_ns=0,
        )
    )

    assert binaries == []


@pytest.mark.asyncio
async def test_tagged_provider_response_rejects_later_untagged_transcript():
    sess = _session(FakeProvider([]))
    tagged_audio = RealtimeEvent(
        type="audio_delta",
        audio=AudioChunk(
            pcm=b"\x10\x00" * 160,
            sample_rate=24_000,
            timestamp_ns=0,
        ),
        provider_turn_id="response-a",
    )

    assert await sess._accept_provider_response_event(tagged_audio)  # noqa: SLF001
    assert await sess._gate.push_audio(  # noqa: SLF001
        tagged_audio.audio,
        response_id="response-a",
    ) == []
    assert not await sess._accept_provider_response_event(  # noqa: SLF001
        RealtimeEvent(
            type="output_transcript_delta",
            text="Stale transcript without an owner.",
        )
    )
    assert sess._gate.release_available() == []  # noqa: SLF001
    assert sess._response_identity_drops == 1  # noqa: SLF001


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


class _SlowDelegateBridgeSession(FakeSession):
    """Slow delegate: expose the dead-air window between dispatch and result."""

    def __init__(
        self,
        events,
        *,
        bridge_sent,
        result_delivered,
        bridge_line="I'm still working on it.",
    ):
        super().__init__(events)
        self._bridge_sent = bridge_sent
        self._result_delivered = result_delivered
        self._bridge_line = bridge_line

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="Write this to my wiki.",
            is_final=True,
        )
        await self._bridge_sent.wait()
        yield RealtimeEvent(
            type="output_transcript_delta",
            text=self._bridge_line,
        )
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(
                pcm=b"\x01\x02" * 8,
                sample_rate=24_000,
                timestamp_ns=0,
            ),
        )
        # The bridge line's own response lifecycle completes long before the
        # delegated result exists; the turn must survive this completion.
        yield RealtimeEvent(type="turn_complete")
        await self._result_delivered.wait()
        yield RealtimeEvent(
            type="output_transcript_delta",
            text="Stored on your page: note.",
        )
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(
                pcm=b"\x03\x04" * 8,
                sample_rate=24_000,
                timestamp_ns=0,
            ),
        )
        yield RealtimeEvent(type="turn_complete")

    async def send_text(self, text):
        await super().send_text(text)
        if "<trusted_action_result>" in text:
            self._result_delivered.set()
        else:
            self._bridge_sent.set()


class _SlowDelegateBridgeProvider(FakeProvider):
    def __init__(
        self,
        *,
        bridge_sent,
        result_delivered,
        bridge_line="I'm still working on it.",
    ):
        super().__init__([])
        self._bridge_sent = bridge_sent
        self._result_delivered = result_delivered
        self._bridge_line = bridge_line

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = _SlowDelegateBridgeSession(
            [],
            bridge_sent=self._bridge_sent,
            result_delivered=self._result_delivered,
            bridge_line=self._bridge_line,
        )
        return self.session


@pytest.mark.asyncio
async def test_slow_deterministic_delegate_speaks_a_bridge_line(monkeypatch):
    """BUG-051: dead air between dispatch and result gets one interim line."""
    monkeypatch.setattr("jarvis.realtime.session._DELEGATE_BRIDGE_DELAY_S", 0.05)
    # Pin the varied progress-line pick to the line the fake session speaks.
    monkeypatch.setattr(
        "jarvis.realtime.session._pick_delegate_bridge_text",
        lambda language: "I'm still working on it.",
    )
    gate = asyncio.Event()
    bridge_sent = asyncio.Event()
    result_delivered = asyncio.Event()
    brain = FakeBrain(replies=("Stored on your page: note.",), gate=gate)
    provider = _SlowDelegateBridgeProvider(
        bridge_sent=bridge_sent, result_delivered=result_delivered
    )
    thinking_sent = asyncio.Event()

    class _StatusMessages(list[dict]):
        def append(self, message: dict) -> None:
            super().append(message)
            if message == {"type": "thinking"}:
                thinking_sent.set()

    jsons = _StatusMessages()
    binaries: list[bytes] = []
    bus = FakeBus()
    sess = _session(
        provider,
        brain=brain,
        jsons=jsons,
        binaries=binaries,
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(bridge_sent.wait(), timeout=2)

    bridge = provider.session.text_inputs[0]
    assert "<trusted_action_result>" not in bridge
    assert "I'm still working on it." in bridge
    # The line is framed as the model's own words, never a quote to perform
    # (a quoted line flipped Gemini's native voice, forensic 2026-07-17).
    assert '"I\'m still working on it."' not in bridge
    assert "same voice" in bridge
    assert "Write this to my wiki." not in bridge
    # While the bridge response is live, provider output must flow.
    assert sess._must_withhold_provider_output() is False

    # Keep the delegate pending until the provider closes the bridge response.
    # Otherwise the fake result can overtake its own interim sentence.
    await asyncio.wait_for(thinking_sent.wait(), timeout=2)
    gate.set()
    await asyncio.wait_for(result_delivered.wait(), timeout=2)
    result = provider.session.text_inputs[-1]
    assert "<trusted_action_result>" in result
    assert "Stored on your page: note." in result
    # The bridge's completed response must not have closed the turn: the
    # result is delivered into the live turn, not as a late follow-up.
    assert "finished only now" not in result
    await sess.wait_finished()
    assert binaries
    spoken = [event for event in bus.events if isinstance(event, SpeechSpoken)]
    assert [(event.text, event.spoken_kind) for event in spoken] == [
        ("I'm still working on it.", "progress"),
        ("Stored on your page: note.", "reply"),
    ]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_capability_limited_provider_acknowledges_delegate_early(monkeypatch):
    """A capability-limited provider streams its trusted acknowledgement early."""
    monkeypatch.setattr("jarvis.realtime.session._DELEGATE_BRIDGE_DELAY_S", 60.0)
    monkeypatch.setattr(
        "jarvis.realtime.session._CAPABILITY_LIMITED_DELEGATE_BRIDGE_DELAY_S",
        0.01,
    )
    monkeypatch.setattr(
        "jarvis.realtime.session._pick_delegate_bridge_text",
        lambda language: "I'm still working on it.",
    )

    speech_requested = asyncio.Event()
    first_audio = asyncio.Event()

    class _AuthoritativeSpeechSession(FakeSession):
        direct_speech_is_authoritative = True

        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Write this to my wiki.",
                is_final=True,
            )
            await speech_requested.wait()
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x01\x00" * 240,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            await asyncio.Event().wait()

        async def send_speech(self, text):
            self.text_inputs.append(text)
            speech_requested.set()

    class _CapabilityLimitedProvider(FakeProvider):
        supports_direct_tools = False

        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _AuthoritativeSpeechSession([])
            return self.session

    class _AudioMessages(list[bytes]):
        def append(self, data: bytes) -> None:
            super().append(data)
            first_audio.set()

    gate = asyncio.Event()
    provider = _CapabilityLimitedProvider([])
    binaries = _AudioMessages()
    sess = _session(
        provider,
        brain=FakeBrain(replies=("Stored.",), gate=gate),
        binaries=binaries,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(first_audio.wait(), timeout=1)

    assert provider.session.text_inputs == ["I'm still working on it."]
    assert binaries
    assert sess._echo_guard.is_echo("I'm still working on it.")
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_varied_bridge_line_passes_validation_and_is_persisted(monkeypatch):
    """A non-default pool line must clear the withhold and reach the record.

    Live feedback 2026-07-17 08:47: the single fixed "Ich bin noch dran."
    line read robotic. The progress line now varies per bridge run; the
    validator accepts exactly the closed localized pool.
    """  # i18n-allow: quoted German forensic phrase
    varied_line = "One moment, almost there."
    monkeypatch.setattr("jarvis.realtime.session._DELEGATE_BRIDGE_DELAY_S", 0.05)
    monkeypatch.setattr(
        "jarvis.realtime.session._pick_delegate_bridge_text",
        lambda language: varied_line,
    )
    gate = asyncio.Event()
    bridge_sent = asyncio.Event()
    result_delivered = asyncio.Event()
    brain = FakeBrain(replies=("Stored on your page: note.",), gate=gate)
    provider = _SlowDelegateBridgeProvider(
        bridge_sent=bridge_sent,
        result_delivered=result_delivered,
        bridge_line=varied_line,
    )
    thinking_sent = asyncio.Event()

    class _StatusMessages(list[dict]):
        def append(self, message: dict) -> None:
            super().append(message)
            if message == {"type": "thinking"}:
                thinking_sent.set()

    jsons = _StatusMessages()
    binaries: list[bytes] = []
    bus = FakeBus()
    sess = _session(
        provider,
        brain=brain,
        jsons=jsons,
        binaries=binaries,
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(bridge_sent.wait(), timeout=2)
    assert varied_line in provider.session.text_inputs[0]

    await asyncio.wait_for(thinking_sent.wait(), timeout=2)
    gate.set()
    await asyncio.wait_for(result_delivered.wait(), timeout=2)
    await sess.wait_finished()
    spoken = [event for event in bus.events if isinstance(event, SpeechSpoken)]
    assert [(event.text, event.spoken_kind) for event in spoken] == [
        (varied_line, "progress"),
        ("Stored on your page: note.", "reply"),
    ]
    await sess.end(reason="test")


class _StalledPromiseSession(FakeSession):
    """Answer with an unbacked action promise, then never complete the turn.

    Live forensic 2026-07-15 07:59: the promise-block guard interrupted a
    response that was already complete on the wire, so no further
    turn_complete arrived. The deterministic recovery then timed out waiting
    for the provider boundary and refused the action outright — the user heard
    a canned failure although the full final input transcript was in hand.
    """

    def __init__(self, *, released):
        super().__init__([])
        self._released = released

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="That is not the right server.",
            is_final=True,
        )
        yield RealtimeEvent(
            type="output_transcript_delta",
            text="I'll check and get back to you.",
        )
        await self._released.wait()


class _StalledPromiseProvider(FakeProvider):
    def __init__(self, *, released):
        super().__init__([])
        self._released = released

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = _StalledPromiseSession(released=self._released)
        return self.session


@pytest.mark.asyncio
async def test_blocked_action_promise_still_dispatches_after_boundary_timeout(
    monkeypatch,
):
    """The promise-block recovery must run the action, not refuse it.

    The input transcript is final by construction on this path (the provider
    already produced a response for it), so a missing provider boundary after
    the interrupt may delay the dispatch but never veto it.
    """
    monkeypatch.setattr(
        "jarvis.realtime.session._DELEGATE_INPUT_BOUNDARY_WAIT_S", 0.05
    )
    released = asyncio.Event()
    brain = FakeBrain(replies=("Switched to the right server.",))
    provider = _StalledPromiseProvider(released=released)
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    async with asyncio.timeout(2):
        while not provider.session.text_inputs:  # noqa: ASYNC110 - fake has no event
            await asyncio.sleep(0.01)

    assert brain.calls, "the recovery must dispatch the brain turn"
    assert brain.calls[0][0] == "That is not the right server."
    result = provider.session.text_inputs[-1]
    assert "<trusted_action_result>" in result
    assert "Switched to the right server." in result
    assert "Result status: success" in result

    released.set()
    await sess.wait_finished()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_fast_deterministic_delegate_needs_no_bridge_line(monkeypatch):
    """A result faster than the bridge delay keeps the turn chatter-free."""
    monkeypatch.setattr("jarvis.realtime.session._DELEGATE_BRIDGE_DELAY_S", 0.15)
    result_delivered = asyncio.Event()

    class _FastSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Write this to my wiki.",
                is_final=True,
            )
            await result_delivered.wait()
            yield RealtimeEvent(type="turn_complete")

        async def send_text(self, text):
            await super().send_text(text)
            if "<trusted_action_result>" in text:
                result_delivered.set()

    class _FastProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _FastSession([])
            return self.session

    provider = _FastProvider([])
    sess = _session(provider, brain=FakeBrain(replies=("Stored.",)))

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(result_delivered.wait(), timeout=2)
    await asyncio.sleep(0.3)  # outlive the bridge delay: it must not fire late
    texts = provider.session.text_inputs
    assert len(texts) == 1
    assert "<trusted_action_result>" in texts[0]
    await sess.wait_finished()
    await sess.end(reason="test")


class _PreemptedDelegateBridgeSession(FakeSession):
    """Hold the bridge response open until the trusted result preempts it."""

    def __init__(self, *, bridge_sent, interrupted, result_delivered):
        super().__init__([])
        self._bridge_sent = bridge_sent
        self._interrupted = interrupted
        self._result_delivered = result_delivered

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="Check my current wiki status.",
            is_final=True,
        )
        await self._bridge_sent.wait()
        yield RealtimeEvent(
            type="output_transcript_delta",
            text="I'm still working on it.",
        )
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(
                pcm=b"\x01\x02" * 8,
                sample_rate=24_000,
                timestamp_ns=0,
            ),
        )
        await self._interrupted.wait()
        yield RealtimeEvent(type="turn_complete")
        await self._result_delivered.wait()
        yield RealtimeEvent(type="turn_complete")

    async def send_text(self, text):
        await super().send_text(text)
        if "<trusted_action_result>" in text:
            self._result_delivered.set()
        else:
            self._bridge_sent.set()

    async def interrupt(self):
        await super().interrupt()
        self._interrupted.set()


class _PreemptedDelegateBridgeProvider(FakeProvider):
    def __init__(self, *, bridge_sent, interrupted, result_delivered):
        super().__init__([])
        self._bridge_sent = bridge_sent
        self._interrupted = interrupted
        self._result_delivered = result_delivered

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = _PreemptedDelegateBridgeSession(
            bridge_sent=self._bridge_sent,
            interrupted=self._interrupted,
            result_delivered=self._result_delivered,
        )
        return self.session


@pytest.mark.asyncio
async def test_ready_result_preempts_active_realtime_bridge(monkeypatch):
    """A finished result must not queue behind an in-flight interim response."""
    monkeypatch.setattr("jarvis.realtime.session._DELEGATE_BRIDGE_DELAY_S", 0.01)
    gate = asyncio.Event()
    bridge_sent = asyncio.Event()
    interrupted = asyncio.Event()
    result_delivered = asyncio.Event()
    bus = FakeBus()
    binaries: list[bytes] = []
    provider = _PreemptedDelegateBridgeProvider(
        bridge_sent=bridge_sent,
        interrupted=interrupted,
        result_delivered=result_delivered,
    )
    brain = FakeBrain(replies=("The grounded figure is 42.",), gate=gate)
    sess = _session(
        provider,
        brain=brain,
        binaries=binaries,
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(bridge_sent.wait(), timeout=2)
    gate.set()
    await asyncio.wait_for(interrupted.wait(), timeout=2)
    await asyncio.wait_for(result_delivered.wait(), timeout=2)

    assert provider.session.interrupts >= 1
    assert binaries == []
    assert not any(isinstance(event, SpeechSpoken) for event in bus.events)
    assert "<trusted_action_result>" in provider.session.text_inputs[-1]
    await sess.wait_finished()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_nonconforming_realtime_bridge_audio_is_never_released(monkeypatch):
    """A hostile bridge cannot turn an ungrounded claim into spoken output."""
    monkeypatch.setattr("jarvis.realtime.session._DELEGATE_BRIDGE_DELAY_S", 0.01)
    gate = asyncio.Event()
    bridge_sent = asyncio.Event()
    bridge_finished = asyncio.Event()
    result_delivered = asyncio.Event()

    class _HostileBridgeSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="List my private notebooks.",
                is_final=True,
            )
            await bridge_sent.wait()
            yield RealtimeEvent(
                type="output_transcript_delta",
                text="Your notebooks are Alpha, Beta, and Gamma.",
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x01\x02" * 8,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            yield RealtimeEvent(type="turn_complete")
            bridge_finished.set()
            await result_delivered.wait()
            yield RealtimeEvent(type="turn_complete")

        async def send_text(self, text):
            await super().send_text(text)
            if "<trusted_action_result>" in text:
                result_delivered.set()
            else:
                bridge_sent.set()

    class _HostileBridgeProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _HostileBridgeSession([])
            return self.session

    bus = FakeBus()
    binaries: list[bytes] = []
    provider = _HostileBridgeProvider([])
    brain = FakeBrain(replies=("Notebook access is unavailable.",), gate=gate)
    sess = _session(
        provider,
        brain=brain,
        binaries=binaries,
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(bridge_finished.wait(), timeout=2)
    assert binaries == []
    assert not any(isinstance(event, SpeechSpoken) for event in bus.events)

    gate.set()
    await asyncio.wait_for(result_delivered.wait(), timeout=2)
    await sess.wait_finished()
    assert all(
        "Alpha, Beta, and Gamma" not in getattr(event, "text", "")
        for event in bus.events
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_realtime_delegate_disables_classic_tool_ack() -> None:
    """Classic pipeline acks stay enabled by default; realtime opts out per turn."""
    brain = FakeBrain(replies=("done",))
    sess = _session(FakeProvider([]), brain=brain)

    assert await sess._dispatch_brain_turn("Check it.") == "done"
    assert brain.calls[0][1]["emit_tool_ack"] is False


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
async def test_direct_mode_recovers_provider_turn_that_has_no_output():
    """A substantive user turn must never complete as successful silence."""
    user_text = "I am bored. What could we do?"
    recovered_reply = "We could build a tiny game together."
    spoken_audio = AudioChunk(
        pcm=b"\x01\x02" * 8,
        sample_rate=24_000,
        timestamp_ns=0,
    )
    brain = FakeBrain(replies=(recovered_reply,))
    provider = AutomaticDelegateProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text=user_text,
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ],
        [
            RealtimeEvent(
                type="output_transcript_delta",
                text=recovered_reply,
                is_final=True,
            ),
            RealtimeEvent(type="audio_delta", audio=spoken_audio),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    jsons: list[dict] = []
    binaries: list[bytes] = []
    bus = FakeBus()
    sess = _session(
        provider,
        brain=brain,
        tool_mode="direct",
        jsons=jsons,
        binaries=binaries,
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=2)

    assert [call[0] for call in brain.calls] == [user_text]
    assert len(provider.session.text_inputs) == 1
    assert "<trusted_action_result>" in provider.session.text_inputs[0]
    assert binaries == [spoken_audio.pcm]
    assert sum(item.get("type") == "turn_complete" for item in jsons) == 1
    completed = [event for event in bus.events if isinstance(event, VoiceTurnCompleted)]
    assert len(completed) == 1
    assert completed[0].jarvis_text == recovered_reply
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_text_only_provider_answer_uses_surface_tts_without_brain_retry():
    """An audio-mode response with text but zero PCM is not treated as spoken."""
    answer = "Here is something useful for you."
    brain = FakeBrain()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Tell me something useful.",
                is_final=True,
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text=answer,
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    bus = FakeBus()
    sess = _session(
        provider,
        brain=brain,
        tool_mode="direct",
        jsons=jsons,
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert brain.calls == []
    assert _spoke_surface_line(jsons, answer)
    completed = [event for event in bus.events if isinstance(event, VoiceTurnCompleted)]
    assert len(completed) == 1
    assert completed[0].jarvis_text == answer
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_empty_provider_turn_without_brain_speaks_local_error():
    """A keyless fallback chain still closes the turn honestly, never silently."""
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Tell me something useful.",
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    bus = FakeBus()
    sess = _session(
        provider,
        brain=None,
        tool_mode="direct",
        jsons=jsons,
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    fallback = "An error occurred."
    assert _spoke_surface_line(jsons, fallback)
    completed = [event for event in bus.events if isinstance(event, VoiceTurnCompleted)]
    assert len(completed) == 1
    assert completed[0].jarvis_text == fallback
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_empty_recovery_response_uses_surface_tts_without_rerunning_brain():
    """A second provider failure speaks the grounded result through local TTS."""
    user_text = "I am bored. What could we do?"
    recovered_reply = "We could build a tiny game together."
    brain = FakeBrain(replies=(recovered_reply,))
    provider = AutomaticDelegateProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text=user_text,
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ],
        [RealtimeEvent(type="turn_complete")],
    )
    jsons: list[dict] = []
    bus = FakeBus()
    sess = _session(
        provider,
        brain=brain,
        tool_mode="direct",
        jsons=jsons,
        bus=bus,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=2)

    assert [call[0] for call in brain.calls] == [user_text]
    assert _spoke_surface_line(jsons, recovered_reply)
    completed = [event for event in bus.events if isinstance(event, VoiceTurnCompleted)]
    assert len(completed) == 1
    assert completed[0].jarvis_text == recovered_reply
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_direct_tool_empty_response_reuses_result_without_repeating_action():
    """A completed tool is rendered again; the user request is never replayed."""
    spoken_audio = AudioChunk(
        pcm=b"\x03\x04" * 8,
        sample_rate=24_000,
        timestamp_ns=0,
    )
    tool_result_sent = asyncio.Event()
    retry_sent = asyncio.Event()

    class _DirectToolRecoverySession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Open the calculator.",
                is_final=True,
            )
            yield RealtimeEvent(
                type="tool_call",
                call_id="direct-empty-1",
                tool_name="open_app",
                tool_args={"app_name": "Calculator"},
            )
            await tool_result_sent.wait()
            yield RealtimeEvent(type="turn_complete")
            await retry_sent.wait()
            yield RealtimeEvent(
                type="output_transcript_delta",
                text="The calculator is open.",
            )
            yield RealtimeEvent(type="audio_delta", audio=spoken_audio)
            yield RealtimeEvent(type="turn_complete")

        async def send_tool_result(self, call_id, name, result):
            await super().send_tool_result(call_id, name, result)
            tool_result_sent.set()

        async def send_text(self, text):
            await super().send_text(text)
            retry_sent.set()

    class _DirectToolRecoveryProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _DirectToolRecoverySession([])
            return self.session

    brain = FakeBrain()
    tool_bridge = FakeToolBridge()
    provider = _DirectToolRecoveryProvider([])
    binaries: list[bytes] = []
    sess = _session(
        provider,
        brain=brain,
        tool_bridge=tool_bridge,
        tool_mode="direct",
        binaries=binaries,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=2)

    assert tool_bridge.calls == [("open_app", {"app_name": "Calculator"})]
    assert brain.calls == []
    assert len(provider.session.text_inputs) == 1
    assert "do not repeat the action" in provider.session.text_inputs[0]
    assert binaries == [spoken_audio.pcm]
    await sess.end(reason="test")


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

    utterance = "Und was ist damit?"  # i18n-allow: German speech-input fixture
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
async def test_native_knowledge_turn_rejects_unnecessary_jarvis_action():
    """A realtime model cannot spend Tool Model quota on public knowledge."""
    from jarvis.brain.turn_planner import plan_turn

    utterance = "Where does Aliko Dangote live?"
    assert plan_turn(utterance).requires_orchestrator is False

    brain = FakeBrain(replies=("This must not be called.",))
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text=utterance,
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="c-native",
                tool_name="jarvis_action",
                tool_args={"request": utterance},
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text="He lives in Lagos.",
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert brain.calls == []
    result = provider.session.tool_results[0][2]
    assert result["success"] is False
    assert "Answer" in result["error"]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_provider_down_apology_is_not_reported_as_delegate_success():
    """A non-empty Brain outage phrase is still a failed Tool Model turn."""
    brain = FakeBrain(replies=("The model connection is unavailable.",))
    brain._last_turn_all_failed = True
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="What is there?",
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="c-down",
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

    result = provider.session.tool_results[0][2]
    assert result["success"] is False
    assert "Tool Model" in result["error"]
    assert result["spoken_reply"] != "The model connection is unavailable."
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_garbled_wiki_follow_up_inherits_session_context_and_delegates():
    """The exact forensic STT output must not depend on model tool discretion."""
    utterance = "Was steht im Mainim drin?"  # i18n-allow: exact German forensic STT
    brain = FakeBrain(replies=("Your Wiki contains three project pages.",))
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text=utterance,
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain)
    sess._remember_delegate_turn(
        "What does a Wiki contain?",
        "A Wiki contains linked pages and revision history.",
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    assert provider.session.required_tools == []
    assert brain.calls[0][0] == utterance
    assert "<trusted_action_result>" in provider.session.text_inputs[-1]
    await sess.end(reason="test")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_mode", ["delegate", "direct"])
async def test_screen_context_always_uses_deterministic_brain_turn(tool_mode: str):
    """The realtime model never answers a visual turn without the image."""
    utterance = "Schau dir bitte meinen Bildschirm an."  # i18n-allow: DE input
    brain = FakeBrain(replies=("The current screen shows the settings view.",))
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text=utterance, is_final=True),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain, tool_mode=tool_mode)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    assert provider.session.response_requests == 0
    assert brain.calls[0][0] == utterance
    assert "<trusted_action_result>" in provider.session.text_inputs[-1]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_native_realtime_promise_without_tool_recovers_via_orchestrator():
    """A provider violation starts the action instead of ending on a promise."""
    utterance = "Was steht im Mainim drin?"  # i18n-allow: exact German forensic STT
    speculative_audio = AudioChunk(
        pcm=b"\x01\x02" * 8,
        sample_rate=24_000,
        timestamp_ns=0,
    )
    brain = FakeBrain(replies=("Your Wiki contains three project pages.",))
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text=utterance,
                is_final=True,
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text=(
                    "Das kann ich gerne für dich "  # i18n-allow
                    "nachschauen. Einen Moment, "  # i18n-allow
                    "ich werfe einen Blick in dein "  # i18n-allow
                    "Wiki und sage dir gleich Bescheid."  # i18n-allow
                ),  # i18n-allow: exact German runtime-output failure shape
            ),
            RealtimeEvent(type="audio_delta", audio=speculative_audio),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    binaries: list[bytes] = []
    sess = _session(
        provider,
        brain=brain,
        jsons=jsons,
        binaries=binaries,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.02)

    assert provider.session.interrupts == 1
    assert provider.session.required_tools == [None]
    assert brain.calls[0][0] == utterance
    provider_received_result = bool(
        provider.session.text_inputs
        and "<trusted_action_result>" in provider.session.text_inputs[-1]
    )
    surface_received_result = any(
        item.get("type") == "error_spoken"
        and item.get("text") == "Your Wiki contains three project pages."
        for item in jsons
    )
    assert provider_received_result or surface_received_result
    assert binaries == []
    assert not any(
        item.get("role") == "assistant" and "Einen Moment" in item.get("text", "")
        for item in jsons
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_direct_realtime_promise_without_tool_fails_closed_honestly():
    """Direct-tool mode cannot silently leave an announced action pending."""
    from jarvis.brain.action_honesty import action_not_started_phrase

    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Could you handle that one?",
                is_final=True,
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text="One moment, I'll check that and get back to you.",
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    sess = _session(provider, brain=FakeBrain(), tool_mode="direct", jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert provider.session.interrupts == 1
    assert _spoke_surface_line(jsons, action_not_started_phrase("en"))
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
                "emit_tool_ack": False,
                "publish_response": False,
                "use_history": False,
                "history_override": (),
                # The session hands the delegate its own resolved output
                # language so a jarvis_action turn cannot answer in a different
                # language than the rest of the conversation (English here).
                "force_output_language": "en",
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
async def test_delegate_empty_spoken_answer_uses_surface_tts_fallback():
    bus = FakeBus()
    brain = FakeBrain(replies=("The action completed.",), bus=bus)
    jsons: list[dict] = []
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
    sess = _session(provider, brain=brain, bus=bus, jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    responses = [event for event in bus.events if isinstance(event, ResponseGenerated)]
    completed = next(event for event in bus.events if isinstance(event, VoiceTurnCompleted))
    assert [event.text for event in responses] == ["The action completed."]
    assert completed.jarvis_text == "The action completed."
    assert _spoke_surface_line(jsons, "The action completed.")
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


@pytest.mark.asyncio
async def test_scrub_cancel_waits_for_audible_receipt_before_claiming_speech():
    """A queued fallback is not evidence that the user heard it.

    Live 2026-08-09 20:47: the Codex subscription has no realtime-scoped
    fallback TTS, yet the session published ``SpeechSpoken`` and the inspector
    claimed "An error occurred" was spoken. The surface owns the playback
    receipt, so the session may only send the request with its audit metadata.
    """
    provider = FakeProvider([])
    bus = FakeBus()
    sent: list[dict] = []

    async def _capture_json(message):
        sent.append(message)

    sess = RealtimeVoiceSession(
        session_id="scrub-cancel-record",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=_capture_json,
        provider=provider,
        config=_cfg(),
        bus=bus,
        surface="desktop",
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    reason = "unsafe output transcript (detectors: replaced_stacktrace)"
    await sess._cancel_unsafe_output(reason=reason)
    await sess.end(reason="test")

    spoken = [event for event in bus.events if isinstance(event, SpeechSpoken)]
    assert spoken == []
    fallbacks = [message for message in sent if message.get("type") == "error_spoken"]
    assert fallbacks[-1]["spoken_kind"] == "withheld"
    assert fallbacks[-1]["detail"] == reason
    assert fallbacks[-1]["text"] == sess._gate.fallback_phrase()


@pytest.mark.asyncio
async def test_scrub_cancel_replaces_the_partial_transcript_with_the_spoken_fallback():
    """The turn's answer is what the user actually hears. Live forensic
    2026-07-17 10:04: the aborted provider rendering left a half sentence
    ("…Im Kalender") as the turn text, so the NEXT turn's delegate history no
    longer knew what was really said and contradicted it. The cancel must
    replace the partial transcript with the spoken fallback."""
    provider = FakeProvider([])
    sess = RealtimeVoiceSession(
        session_id="scrub-cancel-transcript",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        surface="desktop",
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    sess._output_transcript.append("Tomorrow looks relaxed. Your calendar")

    full_reply = (
        "Tomorrow looks relaxed. Your calendar only holds blocked slots, "
        "no real appointments."
    )
    await sess._cancel_unsafe_output(
        reason="output transcript exceeded safe audio buffer",
        fallback_text=full_reply,
    )

    assert "".join(sess._output_transcript) == full_reply
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_scrub_cancel_fallback_carries_the_active_voice_hint():
    """Voice-identity continuity (live forensic 2026-07-17 10:04: Fenrir's
    aborted readback was re-spoken by Charon): the surface fallback names the
    session's active voice so the pipeline TTS can keep speaking with it."""
    provider = FakeProvider([])
    sent: list[dict] = []

    def _capture_json(message):
        sent.append(message)
        return asyncio.sleep(0)

    sess = RealtimeVoiceSession(
        session_id="scrub-cancel-voice-hint",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=_capture_json,
        provider=provider,
        config=_cfg(),
        surface="desktop",
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    sess._active_voice = "Fenrir"

    await sess._cancel_unsafe_output(
        reason="output transcript exceeded safe audio buffer",
        fallback_text="Full grounded reply.",
    )

    fallbacks = [m for m in sent if m.get("type") == "error_spoken"]
    assert fallbacks and fallbacks[-1]["text"] == "Full grounded reply."
    assert fallbacks[-1]["voice"] == "Fenrir"
    await sess.end(reason="test")


# ---------------------------------------------------------------------------
# User agent-instructions (the Ruben.md-equivalent file) in the realtime path
# ---------------------------------------------------------------------------


def test_session_instructions_place_preferences_between_persona_and_directives(
    monkeypatch,
):
    """The user's standing-instructions block must sit right after the persona
    and before the operational directives, so it frames the whole spoken output."""
    from jarvis.brain import persona_loader
    from jarvis.realtime import session as session_mod

    monkeypatch.setattr(
        persona_loader,
        "load_effective_persona_prompt",
        lambda *, compact=False: "PERSONA_MARKER",
    )
    text = session_mod._session_instructions(
        "de",
        preferences="PREFS_MARKER",
        tool_directive="TOOL_MARKER",
    )
    assert "PREFS_MARKER" in text
    assert (
        text.index("PERSONA_MARKER")
        < text.index("PREFS_MARKER")
        < text.index("TOOL_MARKER")
    )


def test_session_instructions_never_teach_native_voice_pipeline_control_tokens(
    monkeypatch,
):
    """The classic TTS sentinel is neither speakable nor valid in realtime."""
    from jarvis.brain import persona_loader
    from jarvis.realtime import session as session_mod
    from jarvis.speech.hangup import END_CALL_SIGNAL

    monkeypatch.setattr(
        persona_loader,
        "load_effective_persona_prompt",
        lambda *, compact=False: (
            "IDENTITY\n\n"
            "ENDING THE CALL\n"
            f"Say goodbye and append {END_CALL_SIGNAL}.\n\n"
            "CONTEXT\nKeep the actual conversation context."
        ),
    )

    text = session_mod._session_instructions("de")

    assert "IDENTITY" in text
    assert "Keep the actual conversation context" in text
    assert "ENDING THE CALL" not in text
    assert END_CALL_SIGNAL not in text
    assert "exactly one assistant response" in text
    assert "never supply the user's side" in text.lower()


def test_session_instructions_anchor_clock_and_stale_knowledge_guard(monkeypatch):
    """The per-turn instructions must carry today's date AND tell the model
    its training knowledge is older than that date. Without the second half
    the model asserts pre-cutoff facts as current (live 2026-07-21: a release
    'planned for 2025' presented as the current state in July 2026)."""
    from datetime import datetime

    from jarvis.brain import persona_loader
    from jarvis.realtime import session as session_mod

    monkeypatch.setattr(
        persona_loader,
        "load_effective_persona_prompt",
        lambda *, compact=False: "PERSONA_MARKER",
    )
    text = session_mod._session_instructions("en")
    assert datetime.now().astimezone().strftime("%Y-%m-%d") in text
    assert "training cutoff" in text
    # The guard must anchor reasoning on the injected clock, not training years.
    assert "current date" in text
    # And it must demand honest dating instead of asserting stale state.
    assert "as of my last information" in text


def test_session_instructions_carry_the_precision_guard(monkeypatch):
    """BUG-106: the freshness guard alone covers only time-sensitive facts.
    The instructions must also forbid presenting remembered niche figures as
    exact and resting categorical feasibility verdicts on them (live
    2026-07-21 11:36: an invented runway length produced a confident,
    wrong 'cannot land there')."""
    from jarvis.brain import persona_loader
    from jarvis.realtime import session as session_mod

    monkeypatch.setattr(
        persona_loader,
        "load_effective_persona_prompt",
        lambda *, compact=False: "PERSONA_MARKER",
    )
    text = session_mod._session_instructions("en")
    assert "Precision guard" in text
    # Niche figures must never be asserted as exact recall...
    assert "Never present a remembered niche figure as exact" in text
    # ...and no flat verdict may be built on one.
    assert "never rest a categorical verdict" in text
    # The escape hatch must stay reachable: "check" is an action request.
    assert "explicit action request" in text


def test_preferences_block_renders_the_user_file_with_the_realtime_cap(monkeypatch):
    from jarvis.brain import agent_instructions
    from jarvis.realtime import session as session_mod

    seen = {}

    def fake_render(config, *, max_chars=None):
        seen["config"] = config
        seen["max_chars"] = max_chars
        return "RENDERED_PREFS"

    monkeypatch.setattr(agent_instructions, "render_for_prompt", fake_render)
    cfg = _cfg()
    assert session_mod._preferences_block(cfg) == "RENDERED_PREFS"
    assert seen["config"] is cfg
    assert seen["max_chars"] == session_mod._PREFERENCES_MAX_CHARS


def test_preferences_block_degrades_to_empty_on_a_read_fault(monkeypatch):
    from jarvis.brain import agent_instructions
    from jarvis.realtime import session as session_mod

    def broken_render(config, *, max_chars=None):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(agent_instructions, "render_for_prompt", broken_render)
    assert session_mod._preferences_block(_cfg()) == ""


@pytest.mark.asyncio
async def test_open_carries_user_agent_instructions_into_session_instructions(
    monkeypatch, tmp_path
):
    """Regression: the realtime engine speaks directly to the user, so the
    user's agent-instructions file must reach the provider's session
    instructions. It previously reached only the classic deep brain, so voice
    preferences (tone, dialect, address) applied on delegated turns but were
    silently ignored on every direct realtime reply."""
    from jarvis.brain import agent_instructions
    from jarvis.core import config as core_config

    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)
    cfg = _cfg()
    agent_instructions.save_agent_instructions(
        cfg, "Always speak with a Bavarian accent."
    )

    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="hello", is_final=True),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="s-prefs",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=cfg,
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    opened = provider.opened_with.instructions
    assert "Always speak with a Bavarian accent." in opened
    assert "USER PREFERENCES & STANDING INSTRUCTIONS" in opened
    # The per-turn session update must re-read the file too — an edit applies
    # on the next message, exactly as the Settings view promises.
    updated = provider.session.session_updates[-1]["instructions"]
    assert "Always speak with a Bavarian accent." in updated


@pytest.mark.asyncio
async def test_presence_check_vocabulary_matches_probes_only():
    from jarvis.realtime.session import _is_presence_check

    # i18n-allow: German/Spanish speech-input fixtures (matching data)
    assert _is_presence_check("Ja, hallo.")
    assert _is_presence_check("Hallo?")
    assert _is_presence_check("hallo hallo")
    assert _is_presence_check("Bist du noch da?")
    assert _is_presence_check("Hey, bist du noch dran?")
    assert _is_presence_check("Hörst du mich?")  # i18n-allow: fixture
    assert _is_presence_check("Hello? Are you there?")
    assert _is_presence_check("can you hear me")
    assert _is_presence_check("Hola, ¿sigues ahí?")

    # A lone filler is an answer to an open question, never a probe.
    assert not _is_presence_check("Ja.")
    assert not _is_presence_check("yes")
    # Substantive turns must stay with the provider.
    # i18n-allow: German speech-input fixtures (matching data) below
    assert not _is_presence_check("Kann ich die einfach so kaufen?")  # i18n-allow: fixture
    assert not _is_presence_check("hallo kannst du mir das wetter sagen")  # i18n-allow: fixture
    assert not _is_presence_check("are you there tomorrow morning as well")
    assert not _is_presence_check("")


@pytest.mark.asyncio
async def test_presence_check_during_pending_action_gets_status_line_not_provider():
    """A bare "hello?" into a running action gets the deterministic line.

    Live forensic 2026-07-17 09:23: a scrub hold silenced the running answer,
    the user probed with a bare greeting, and the provider replied with a
    fresh-conversation greeting while the delegated answer was still being
    computed. The orchestrator must own that turn: progress line through the
    surface TTS, no provider response, no second brain dispatch.
    """
    from jarvis.realtime.session import _DELEGATE_BRIDGE_TEXTS

    gate = asyncio.Event()  # never set: the delegated action outlives the test
    brain = FakeBrain(gate=gate)
    jsons = []
    # The live shape (2026-07-17 09:23): the provider itself called
    # jarvis_action, the user barged into the silent wait, and only then did
    # the probe's final transcript arrive on a fresh turn.
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Can I just buy it like that?",
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="call-1",
                tool_name="jarvis_action",
                tool_args={"request": "Can I just buy it like that?"},
            ),
            RealtimeEvent(type="interrupted"),
            RealtimeEvent(
                type="input_transcript",
                text="Ja, hallo.",  # i18n-allow: German speech-input fixture
                is_final=True,
            ),
        ]
    )
    sess = _session(provider, brain=brain, jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    assert len(spoken) == 1
    pool = {text for texts in _DELEGATE_BRIDGE_TEXTS.values() for text in texts}
    assert spoken[0]["text"] in pool
    # The probe itself never becomes a provider response or a brain turn:
    # the single request belongs to the first (native) turn.
    assert provider.session.response_requests == 1
    assert all("hallo" not in call[0] for call in brain.calls)
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_presence_check_without_pending_action_stays_native():
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Ja, hallo.",  # i18n-allow: German speech-input fixture
                is_final=True,
            ),
        ]
    )
    sess = _session(provider, brain=FakeBrain(), jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    assert [m for m in jsons if m.get("type") == "error_spoken"] == []
    assert provider.session.required_tools == [None]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_session_end_names_the_delegated_request_it_retains(caplog):
    """A hangup mid-action must name and retain the result delivery debt."""
    import logging as _logging

    gate = asyncio.Event()
    brain = FakeBrain(gate=gate)
    bus = FakeBus()
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Write the travel plan to my wiki.",
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain, bus=bus)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    with caplog.at_level(_logging.INFO, logger="jarvis.realtime.session"):
        await sess.end(reason="hotkey")

    lost = [
        record
        for record in caplog.records
        if "still running" in record.getMessage()
    ]
    assert len(lost) == 1
    assert "travel plan" in lost[0].getMessage()
    assert "retaining it for exactly-once delivery" in lost[0].getMessage()
    assert brain.cancelled is False

    gate.set()
    for _ in range(100):
        if any(isinstance(event, AnnouncementRequested) for event in bus.events):
            break
        await asyncio.sleep(0.01)
    assert len(
        [event for event in bus.events if isinstance(event, AnnouncementRequested)]
    ) == 1


async def test_hangup_abandons_a_coding_brief_that_is_still_being_written(
    monkeypatch,
):
    """The ONE piece of delegated work a hangup drops instead of retaining.

    Maintainer decision 2026-08-13: hanging up ends the order for a workspace
    pane. Composing a brief takes 10-30 s and the PTY write is its last step, so
    abandoning it leaves the pane untouched — unlike a sent mail or a spawned
    mission, which is why the retention above stays the rule for everything else.
    """
    from jarvis.agentic_ide import fanout as ide_fanout

    reasons: list[str] = []
    monkeypatch.setattr(
        ide_fanout,
        "cancel_spoken_deliveries",
        lambda *, reason="": reasons.append(reason) or 1,
    )

    sess = _session(FakeProvider([]))
    await sess.end(reason="hotkey")

    assert len(reasons) == 1
    assert "hotkey" in reasons[0]


async def test_a_handover_keeps_the_coding_brief_alive(monkeypatch):
    """desktop_fallback is not a hangup: the same call continues elsewhere."""
    from jarvis.agentic_ide import fanout as ide_fanout

    called: list[str] = []
    monkeypatch.setattr(
        ide_fanout,
        "cancel_spoken_deliveries",
        lambda *, reason="": called.append(reason) or 0,
    )

    sess = _session(FakeProvider([]))
    await sess.end(reason="desktop_fallback")

    assert called == []


# ---------------------------------------------------------------------------
# BUG-071: in-place transport rebuild after a mid-call provider death
# ---------------------------------------------------------------------------


class DyingSession(FakeSession):
    """Yield the scripted events, then die like a dropped WebSocket."""

    rebuild_on_transport_death = True

    def __init__(self, events, error="1006 None. abnormal closure [internal]"):
        super().__init__(events)
        self._error = error

    async def receive(self):
        for ev in self._events:
            yield ev
            await asyncio.sleep(0)
        raise RuntimeError(self._error)


class EndingSession(FakeSession):
    """Yield the scripted events, then end the iterator cleanly (a graceful
    server close, e.g. Gemini's Live-API session limit)."""

    rebuild_on_transport_death = True


class StayOpenSession(FakeSession):
    """Yield the scripted events, then stay open like a healthy live call."""

    rebuild_on_transport_death = True

    def __init__(self, events):
        super().__init__(events)
        self._released = asyncio.Event()

    async def receive(self):
        for ev in self._events:
            yield ev
            await asyncio.sleep(0)
        await self._released.wait()

    async def close(self):
        await super().close()
        self._released.set()


class WriteStalledSession(StayOpenSession):
    """Keep receiving while microphone writes block forever."""

    async def send_audio(self, chunk):
        del chunk
        await asyncio.Event().wait()


class RebuildingProvider(FakeProvider):
    """Serve a scripted sequence of session objects, one per open_session."""

    def __init__(self, session_factories):
        super().__init__([])
        self._session_factories = list(session_factories)
        self.open_calls = 0
        self.sessions = []
        self.opened_cfgs = []

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.opened_cfgs.append(cfg)
        self.open_calls += 1
        factory = self._session_factories[
            min(self.open_calls - 1, len(self._session_factories) - 1)
        ]
        self.session = factory()
        self.sessions.append(self.session)
        return self.session


async def _wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 - shared bounded test helper
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_transport_death_rebuilds_the_session_in_place():
    """BUG-071: Gemini dropped the Live WebSocket (1006 abnormal closure)
    mid-call and the whole call ended with reason=error although the user
    never hung up. A provider session that declares
    rebuild_on_transport_death gets a fresh transport in place; the surfaces
    see one new audio_ready, never a session end."""
    provider = RebuildingProvider(
        [
            lambda: DyingSession(
                [
                    RealtimeEvent(
                        type="input_transcript", text="hello", is_final=True
                    ),
                    RealtimeEvent(type="turn_complete"),
                ]
            ),
            lambda: StayOpenSession(
                [
                    RealtimeEvent(
                        type="input_transcript",
                        text="still there?",
                        is_final=True,
                    ),
                    RealtimeEvent(type="turn_complete"),
                ]
            ),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="rebuild-inplace",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    # The rebuilt transport must serve real turns, not merely exist.
    await _wait_until(
        lambda: len(provider.sessions) == 2
        and provider.sessions[1].response_requests >= 1
    )

    assert provider.open_calls == 2
    assert not sess.failed
    assert [m for m in jsons if m.get("type") == "provider_error"] == []
    assert len([m for m in jsons if m.get("type") == "audio_ready"]) == 2
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_gemini_live_spending_cap_crosses_family_without_retrying():
    """The live 1011 spending-cap close must cross from Gemini to OpenAI."""
    gemini = RebuildingProvider(
        [
            lambda: DyingSession(
                [],
                error=(
                    "1011 None. Your project has exceeded its monthly "
                    "spending cap. Please go to AI Studio to manage your project"
                ),
            )
        ]
    )
    gemini.name = "gemini-live"
    gemini.credential_family = "gemini"
    openai = RebuildingProvider([lambda: StayOpenSession([])])
    openai.name = "openai-realtime"
    openai.credential_family = "openai"
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="gemini-cap-cross-family",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        providers=[gemini, openai],
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        # Wait for the COMPLETED fallback (active_provider flipped), never for
        # open_calls alone: open_session returning is one await ahead of
        # _open assigning self._provider, and the py3.11 Windows CI leg lost
        # that race deterministically. The finally matters just as much — an
        # assert that fires while the pump still lives leaves a task behind
        # whose shutdown cancel can be LOST on the 3.11 proactor loop
        # (BUG-081's general form), wedging the whole job until its timeout.
        await _wait_until(lambda: sess.active_provider == "openai-realtime")

        assert gemini.open_calls == 1
        assert openai.open_calls == 1
        assert not sess.failed
        assert [m for m in jsons if m.get("type") == "provider_error"] == []
        fallback = next(
            m
            for m in jsons
            if m.get("type") == "provider_fallback"
            and m.get("provider") == "gemini-live"
        )
        assert fallback["status"] == "no_credits"
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_openai_insufficient_quota_event_crosses_to_gemini():
    """A failed response.done can be account-terminal despite recoverable=True."""
    openai = RebuildingProvider(
        [
            lambda: FakeSession(
                [
                    RealtimeEvent(
                        type="error",
                        error=(
                            "RateLimitError: 429 insufficient_quota; check your "
                            "plan and billing details"
                        ),
                        recoverable=True,
                    )
                ]
            )
        ]
    )
    openai.name = "openai-realtime"
    openai.credential_family = "openai"
    gemini = RebuildingProvider([lambda: StayOpenSession([])])
    gemini.name = "gemini-live"
    gemini.credential_family = "gemini"
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="openai-quota-cross-family",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        providers=[openai, gemini],
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        # Same discipline as the gemini-cap test above: wait for the
        # completed flip, and always end the session even when an assert
        # fires, so no live pump task survives into loop teardown.
        await _wait_until(lambda: sess.active_provider == "gemini-live")

        assert openai.open_calls == 1
        assert gemini.open_calls == 1
        assert not sess.failed
        assert [m for m in jsons if m.get("type") == "provider_error"] == []
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_terminal_account_failure_without_alternate_does_not_reconnect():
    """No alternate means one honest failure, not three futile reconnects."""
    gemini = RebuildingProvider(
        [
            lambda: DyingSession(
                [],
                error=(
                    "1011 None. Your project has exceeded its monthly "
                    "spending cap"
                ),
            )
        ]
    )
    gemini.name = "gemini-live"
    gemini.credential_family = "gemini"
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="gemini-cap-no-alternate",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=gemini,
        config=_cfg(),
        bus=None,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=2)

    assert sess.failed
    assert gemini.open_calls == 1
    assert len([m for m in jsons if m.get("type") == "provider_error"]) == 1
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_audio_send_timeout_rebuilds_without_ending_the_call(monkeypatch):
    """A write-only stall and a later stale timeout preserve the live call."""
    import jarvis.realtime.session as session_module

    class _ObservedWriteStalledSession(WriteStalledSession):
        def __init__(self):
            super().__init__([])
            self.send_entered = [asyncio.Event(), asyncio.Event()]
            self._send_count = 0

        async def send_audio(self, chunk):
            del chunk
            index = self._send_count
            self._send_count += 1
            self.send_entered[index].set()
            await asyncio.Event().wait()

    stalled = _ObservedWriteStalledSession()
    monkeypatch.setattr(session_module, "_AUDIO_SEND_TIMEOUT_S", 0.02)
    provider = RebuildingProvider(
        [
            lambda: stalled,
            lambda: StayOpenSession([]),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="rebuild-audio-send-timeout",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
        browser_sample_rate=16_000,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    pump = sess._pump_task

    first_send = asyncio.create_task(
        sess.handle_audio_frame(b"\x00\x01" * 16)
    )
    await asyncio.wait_for(stalled.send_entered[0].wait(), timeout=0.5)
    # ``wait_for`` captured the short deadline for send one. Give send two a
    # longer deadline so the first timeout deterministically rebuilds the
    # transport before the overlapping stale timeout is delivered.
    monkeypatch.setattr(session_module, "_AUDIO_SEND_TIMEOUT_S", 0.15)
    overlapping_send = asyncio.create_task(
        sess.handle_audio_frame(b"\x01\x02" * 16)
    )
    await asyncio.wait_for(stalled.send_entered[1].wait(), timeout=0.5)
    await first_send
    await _wait_until(lambda: provider.open_calls == 2)
    await overlapping_send

    assert sess._pump_task is pump
    assert pump is not None and not pump.done()
    assert not sess.failed
    assert provider.sessions[0].closed
    assert provider.open_calls == 2
    assert len([item for item in jsons if item.get("type") == "audio_ready"]) == 2

    await sess.handle_audio_frame(b"\x02\x03" * 16)
    assert len(provider.sessions[1].sent_audio) == 1

    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_transport_death_without_capability_keeps_terminal_semantics():
    """Adapters that self-heal internally (openai_realtime's BUG-064 stack)
    or declare nothing keep today's contract: a dead receive loop fails the
    session honestly instead of being rebuilt behind their back."""

    class TerminalDyingSession(FakeSession):
        async def receive(self):
            for ev in self._events:
                yield ev
                await asyncio.sleep(0)
            raise RuntimeError("socket vanished")

    class TerminalProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = TerminalDyingSession(self._events)
            return self.session

    jsons = []
    sess = RealtimeVoiceSession(
        session_id="terminal-death",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=TerminalProvider([]),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=5)

    assert sess.failed
    assert any(m.get("type") == "provider_error" for m in jsons)
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_transport_rebuild_storm_fails_honestly():
    """A flapping transport must not reconnect-storm: after the rolling
    budget is spent, the session fails terminally with one honest error."""
    from jarvis.realtime import session as session_mod

    provider = RebuildingProvider([lambda: DyingSession([])])
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="rebuild-storm",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=5)

    assert sess.failed
    assert "keeps dying" in sess.failure_detail
    assert provider.open_calls == 1 + session_mod._TRANSPORT_REBUILD_MAX_PER_WINDOW
    errors = [m for m in jsons if m.get("type") == "provider_error"]
    assert len(errors) == 1
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_second_rapid_rebuild_drops_the_poisoned_history_seed():
    """BUG-104: Gemini's server rejected the conversation seed with 1007
    right after every rebuilt connection reported ready — the client-side
    seed guard never sees a server-side rejection, so each seeded rebuild
    died again, three rebuilds burned the whole recovery budget in ~1.5 s,
    and the call hung up with reason=error mid-sentence. The second rapid
    death in the window must retry WITHOUT the seed: amnesiac but alive."""
    from jarvis.core.protocols import BrainMessage

    provider = RebuildingProvider(
        [
            lambda: DyingSession([]),
            lambda: DyingSession([]),
            lambda: StayOpenSession([]),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="rebuild-poisoned-seed",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _m: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    sess._delegate_history = [
        BrainMessage(role="user", content="what about private law"),
        BrainMessage(role="assistant", content="private autonomy governs it"),
    ]
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(lambda: provider.open_calls >= 3)

    assert not sess.failed
    # Initial open + first rebuild carry the call transcript (BUG-088)...
    assert provider.opened_cfgs[0].history
    assert provider.opened_cfgs[1].history
    # ...but the rebuild after another immediate death goes seedless.
    assert provider.opened_cfgs[2].history == ()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_idle_stream_end_with_capability_rebuilds_instead_of_ending():
    """A graceful provider close at an idle boundary (Live-API session limit)
    still ends the whole CALL on the desktop surface, so a rebuild-capable
    provider is reopened instead of hanging up with reason=error."""
    provider = RebuildingProvider(
        [
            lambda: EndingSession(
                [
                    RealtimeEvent(
                        type="input_transcript", text="hi", is_final=True
                    ),
                    RealtimeEvent(type="turn_complete"),
                ]
            ),
            lambda: StayOpenSession([]),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="rebuild-idle-end",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(lambda: provider.open_calls == 2)

    assert not sess.failed
    assert [m for m in jsons if m.get("type") == "provider_error"] == []
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_mid_turn_stream_end_with_capability_salvages_then_rebuilds():
    """A transport dying mid-reply still releases the transcript-cleared
    audio tail before the rebuild — the fix must not chop the answer harder
    than the transport failure requires."""
    provider = RebuildingProvider(
        [
            lambda: EndingSession(
                [
                    RealtimeEvent(
                        type="input_transcript", text="hi", is_final=True
                    ),
                    RealtimeEvent(
                        type="output_transcript_delta", text="Hi there."
                    ),
                    RealtimeEvent(
                        type="audio_delta",
                        audio=AudioChunk(
                            pcm=b"\x01\x02" * 8,
                            sample_rate=24000,
                            timestamp_ns=0,
                        ),
                    ),
                ]
            ),
            lambda: StayOpenSession([]),
        ]
    )
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="rebuild-mid-turn",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(lambda: provider.open_calls == 2)

    assert not sess.failed
    assert binaries  # the cleared audio tail was salvaged, not dropped
    assert [m for m in jsons if m.get("type") == "provider_error"] == []
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_transport_rebuild_mirrors_the_frozen_turn_to_the_surface():
    """BUG-085: a dead transport never delivers its own turn_complete, so
    after the in-place rebuild the desktop surface stayed in its half-duplex
    'assistant is speaking' echo-guard state forever — every microphone frame
    was fed only to the local barge-in detector, never uploaded, and the
    freshly rebuilt session heard nothing (live forensic 2026-07-18 16:17:
    Gemini's Live-API session limit aborted the connection as turn 21
    drained; the user spoke into a swallowed microphone for 20 s). The
    rebuild must mirror the frozen turn to the surface: one turn_complete
    BEFORE the fresh audio_ready."""
    provider = RebuildingProvider(
        [
            lambda: EndingSession(
                [
                    RealtimeEvent(
                        type="input_transcript", text="hi", is_final=True
                    ),
                    RealtimeEvent(
                        type="output_transcript_delta", text="Hi there."
                    ),
                    RealtimeEvent(
                        type="audio_delta",
                        audio=AudioChunk(
                            pcm=b"\x01\x02" * 8,
                            sample_rate=24000,
                            timestamp_ns=0,
                        ),
                    ),
                ]
            ),
            lambda: StayOpenSession([]),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="rebuild-surface-mirror",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(
        lambda: len([m for m in jsons if m.get("type") == "audio_ready"]) == 2
    )

    kinds = [str(m.get("type", "")) for m in jsons]
    # The scripted first transport dies mid-turn WITHOUT a provider
    # turn_complete event, so the single one here must come from the rebuild.
    assert kinds.count("turn_complete") == 1
    second_ready = [i for i, k in enumerate(kinds) if k == "audio_ready"][1]
    assert kinds.index("turn_complete") < second_ready
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_transport_rebuild_seeds_the_call_history_into_the_fresh_session():
    """BUG-088: an in-place transport rebuild used to hand the fresh provider
    session a completely empty conversation, so the native voice model
    answered every follow-up with amnesia ("what is the hardest language?"
    lost its programming-language framing from earlier turns). The rebuild
    open must carry the bounded call transcript; the first open of a call
    stays seedless."""
    provider = RebuildingProvider(
        [
            lambda: DyingSession(
                [
                    RealtimeEvent(
                        type="input_transcript",
                        text="what is the hardest language",
                        is_final=True,
                    ),
                    RealtimeEvent(
                        type="output_transcript_delta",
                        text="Malbolge is widely feared.",
                    ),
                    RealtimeEvent(type="turn_complete"),
                ]
            ),
            lambda: StayOpenSession([]),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="rebuild-history-seed",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(lambda: len(provider.sessions) == 2)

    assert provider.opened_cfgs[0].history == ()
    assert provider.opened_cfgs[1].history == (
        {"role": "user", "text": "what is the hardest language"},
        {"role": "assistant", "text": "Malbolge is widely feared."},
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_completed_turns_refresh_the_session_history_snapshot():
    """The orchestrator must keep a snapshot-capable provider session current
    after every completed turn (BUG-088), so a provider-internal transport
    rebuild (openai_realtime's BUG-064 stack) can restore the conversation
    without a wire call."""

    class SnapshotSession(FakeSession):
        def __init__(self, events):
            super().__init__(events)
            self.history_snapshots = []

        def set_history_snapshot(self, history):
            self.history_snapshots.append(history)

    class SnapshotProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = SnapshotSession(self._events)
            return self.session

    provider = SnapshotProvider(
        [
            RealtimeEvent(type="input_transcript", text="hello", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text="Hi there."),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="history-snapshot",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _m: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(lambda: bool(provider.session.history_snapshots))

    assert provider.session.history_snapshots[-1] == (
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "Hi there."},
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_transport_death_after_end_call_converts_to_hangup():
    """The user already asked to end the call (end_call acknowledged); a
    dead transport cannot speak the goodbye. The session must end as the
    requested hangup — never as an error, never through a rebuild."""
    provider = RebuildingProvider([lambda: DyingSession([])])
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="rebuild-end-after-turn",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    sess._end_after_turn = True
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=5)

    assert sess.hangup_reason == "voice_pattern"
    assert not sess.failed
    assert provider.open_calls == 1
    assert any(m.get("type") == "hangup" for m in jsons)
    await sess.end(reason="test")


# ---------------------------------------------------------------------------
# Proactive GoAway reconnect: a provider's pre-disconnect notice rebuilds the
# transport inside the announced window instead of waiting for the forced
# close (live 2026-07-21 11:14: the 1008 close raced the recovery chain into
# a cross-provider fallback whose only alternative was quota-dead, and the
# call ended reason=error after 17 turns).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advised_reconnect_rebuilds_immediately_when_idle():
    provider = RebuildingProvider(
        [
            lambda: StayOpenSession(
                [
                    RealtimeEvent(
                        type="error",
                        error="Gemini Live requested reconnect (time_left=50s)",
                        recoverable=True,
                        reconnect_advised=True,
                    ),
                ]
            ),
            lambda: StayOpenSession([]),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="goaway-idle-rebuild",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    # No turn boundary is ever yielded: the idle call must rebuild at once.
    # Wait on the surface-visible observable (the rebuilt transport's
    # audio_ready), not the provider-side open counter: open_session returns
    # BEFORE the session publishes audio_ready, and on a slow event loop
    # (windows-latest CI) asserting right after open_calls==2 raced the
    # delivery (CI 2026-07-21: 1 audio_ready observed, rebuild fine).
    await _wait_until(
        lambda: len([m for m in jsons if m.get("type") == "audio_ready"]) == 2
    )

    assert not sess.failed
    assert [m for m in jsons if m.get("type") == "provider_error"] == []
    assert provider.open_calls == 2
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_advised_reconnect_defers_to_the_turn_boundary_mid_turn():
    provider = RebuildingProvider(
        [
            lambda: StayOpenSession(
                [
                    RealtimeEvent(
                        type="input_transcript", text="hello", is_final=True
                    ),
                    # The response for this turn is now pending — the advised
                    # rebuild must NOT tear the transport down mid-turn.
                    RealtimeEvent(
                        type="error",
                        error="Gemini Live requested reconnect (time_left=50s)",
                        recoverable=True,
                        reconnect_advised=True,
                    ),
                    RealtimeEvent(type="turn_complete"),
                ]
            ),
            lambda: StayOpenSession([]),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="goaway-boundary-rebuild",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    # Same slow-loop race as the idle test: wait for the rebuilt transport's
    # audio_ready itself, not the provider-side open counter.
    await _wait_until(
        lambda: len([m for m in jsons if m.get("type") == "audio_ready"]) == 2
    )

    assert not sess.failed
    assert [m for m in jsons if m.get("type") == "provider_error"] == []
    assert provider.open_calls == 2
    # The first turn's boundary reached the surface before the rebuild's
    # audio_ready: the reply was never cut mid-turn.
    types = [m.get("type") for m in jsons]
    assert types.index("turn_complete") < len(types) - 1 - types[::-1].index(
        "audio_ready"
    )
    await sess.end(reason="test")


def test_the_socket_courtesy_never_outlasts_what_waits_for_the_microphone():
    """A hangup made to free the microphone must not hold it for the wait.

    Live 2026-08-06 17:42: the dictation key hung the call up, the provider
    socket took its full 5 s bound to give up — the same 5 s the handover was
    willing to wait — and the press was refused with "nothing was recorded".
    The close is a best-effort courtesy; the person reaching for the key is
    not. Pinned as a RELATIONSHIP, because the bug was the two numbers being
    equal, not either number itself.
    """
    from jarvis.realtime.session import _PROVIDER_CLOSE_BOUND_S
    from jarvis.speech.pipeline import _DICTATION_HANDOVER_TIMEOUT_S

    assert _PROVIDER_CLOSE_BOUND_S * 2 <= _DICTATION_HANDOVER_TIMEOUT_S


@pytest.mark.asyncio
async def test_the_same_advice_right_after_a_rebuild_ends_the_call_honestly():
    """BUG-124: a rebuild that has to be repeated for the same cause failed.

    Live 2026-08-06 17:41: the self-dialogue advice rebuilt the transport at
    :53, came straight back at :56, rebuilt again at :59. A third would have
    exhausted the budget and ended the call as reason=error anyway — by a
    worse route, after two pointless handshakes. Stop at the relapse and say
    why. Deliberately no cross-provider failover: a subscription-backed
    provider must never fall through to metered voice.
    """
    advice = RealtimeEvent(
        type="error",
        error="the far end is answering itself",
        recoverable=True,
        reconnect_advised=True,
    )
    provider = RebuildingProvider(
        [
            lambda: StayOpenSession([advice]),
            # The fresh transport walks straight back into the same fault.
            lambda: StayOpenSession([advice]),
            lambda: StayOpenSession([]),
        ]
    )
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="relapsed-rebuild",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(lambda: sess.failed)

    # Exactly one rebuild was attempted; the relapse ended the call instead of
    # spending the rest of the budget on transports that cannot help.
    assert provider.open_calls == 2
    errors = [m for m in jsons if m.get("type") == "provider_error"]
    assert errors, "a call that stops must say so"
    assert "after a transport rebuild" in str(errors[-1].get("error"))
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_provider_cannot_re_run_the_order_already_being_executed():
    """One spoken order must reach the world exactly once.

    Live 2026-07-27 20:12: the orchestrator dispatched the user's order
    deterministically (the shared planner wanted an action), the provider then
    finished its own pass over the same audio, opened a FRESH turn and called
    ``jarvis_action`` for the same order — so one Agentic-IDE pane was briefed
    with two different tasks 42 s apart while two idle panes got nothing. The
    per-turn de-duplication cannot see it: the repeat arrives one turn late.
    """
    gate = asyncio.Event()  # the first action stays in flight for the test
    dispatched = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatched.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(gate=gate)

    class _RepeatSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Write that to the wiki.",
                is_final=True,
            )
            await dispatched.wait()
            # Speaking into the waiting silence rolls the turn over while the
            # action still runs — the live shape, and what the per-turn
            # de-duplication cannot see.
            yield RealtimeEvent(type="speech_started")
            # The new turn carries no request of its own.
            yield RealtimeEvent(
                type="input_transcript",
                text="okay okay okay",
                is_final=True,
            )
            yield RealtimeEvent(
                type="tool_call",
                call_id="repeat-1",
                tool_name="jarvis_action",
                tool_args={"request": "Write that to the wiki."},
            )

    class _RepeatProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _RepeatSession([])
            return self.session

    provider = _RepeatProvider([])
    jsons = []
    sess = _session(provider, brain=brain, jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.1)

    assert len(brain.calls) == 1, (
        f"the order must be executed exactly once, got {brain.calls}"
    )
    assert provider.session.tool_results, "the provider must hear a verdict"
    _call_id, name, result = provider.session.tool_results[-1]
    assert name == "jarvis_action"
    assert result["success"] is False
    assert "already being executed" in result["error"]
    # Refusing must not trade the duplicate for silence: provider output is
    # withheld while the action runs, so the orchestrator answers the turn —
    # exactly once, not once more from the no-audio rescue.
    spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    assert len(spoken) == 1, f"expected one spoken progress line, got {spoken}"
    gate.set()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_a_new_order_still_reaches_the_orchestrator_while_one_runs():
    """The repeat guard must not swallow a SECOND, genuinely new request.

    Guard-rail for the fix above: "and Blake should do it too" spoken while
    the first action is still running is a new order, not the old one coming
    back around, and it has to be dispatched.
    """
    gate = asyncio.Event()
    dispatched = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatched.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(gate=gate)

    class _SecondOrderSession(FakeSession):
        async def receive(self):
            yield RealtimeEvent(
                type="input_transcript",
                text="Write that to the wiki.",
                is_final=True,
            )
            await dispatched.wait()
            yield RealtimeEvent(type="speech_started")
            yield RealtimeEvent(
                type="input_transcript",
                text="Now open the settings view as well.",
                is_final=True,
            )
            yield RealtimeEvent(
                type="tool_call",
                call_id="second-1",
                tool_name="jarvis_action",
                tool_args={"request": "Open the settings view."},
            )

    class _SecondOrderProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _SecondOrderSession([])
            return self.session

    provider = _SecondOrderProvider([])
    sess = _session(provider, brain=brain)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.1)

    assert len(brain.calls) == 2, (
        f"the second order must still be dispatched, got {brain.calls}"
    )
    refusals = [
        result
        for _call_id, _name, result in provider.session.tool_results
        if not result.get("success")
        and "already being executed" in str(result.get("error", ""))
    ]
    assert refusals == [], "a new order must never be refused as a repeat"
    gate.set()
    await sess.end(reason="test")


# ---------------------------------------------------------------------------
# Subscription-transport parity: the capability tuple no other fake carries.
#
# The ChatGPT-subscription voice ships with creates_responses_automatically +
# isolates_response_generations + NO native tools + authoritative direct speech
# + a rebuildable transport, on a half-duplex desktop surface. Every fake above
# tests one of those in isolation; the wedges live in the COMBINATION, because
# on that transport the microphone is both the thing half-duplex mutes and the
# only source of the speech edges that would unmute it.
# ---------------------------------------------------------------------------


class SubscriptionLikeSession(FakeSession):
    """A queue-driven session with the live subscription capability tuple."""

    supports_tool_updates = False
    supports_direct_tools = False
    creates_responses_automatically = True
    isolates_response_generations = True
    direct_speech_is_authoritative = True
    rebuild_on_transport_death = True

    def __init__(self, events=None):
        super().__init__(list(events or []))
        self.queue: asyncio.Queue = asyncio.Queue()
        self.spoken = []

    async def receive(self):
        for event in self._events:
            yield event
            await asyncio.sleep(0)
        while True:
            event = await self.queue.get()
            if event is None:
                return
            yield event

    async def send_speech(self, text):
        self.spoken.append(text)

    async def send_tool_result(self, call_id, name, result):
        # The real transport has no function-call wire and can only raise.
        raise RuntimeError("this transport does not execute tools directly")


class SubscriptionLikeProvider(FakeProvider):
    name = "subscription-like"
    supports_direct_tools = False
    handshake_budget_s = 45.0

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = SubscriptionLikeSession(self._events)
        return self.session


def _pcm_chunk(samples=240):
    return AudioChunk(
        pcm=b"\x10\x20" * samples, sample_rate=24_000, timestamp_ns=0
    )


def _half_duplex_session(provider, *, brain=None, jsons=None, binaries=None):
    """A desktop-shaped session: half-duplex, exactly like the live surface."""
    return RealtimeVoiceSession(
        session_id="subscription-test",
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
        config=_delegate_cfg("delegate"),
        brain=brain,
        half_duplex=True,
        surface="desktop",
    )


async def _microphone_reaches(sess, session) -> bool:
    """Whether a microphone frame still gets through to the transport."""
    before = len(session.sent_audio)
    await sess.handle_audio_frame(b"\x00\x01" * 480)
    return len(session.sent_audio) > before


@pytest.mark.asyncio
async def test_subscription_capability_tuple_survives_three_turns():
    """Turn 2 and 3 must work exactly like turn 1.

    The reported failure was "transcription only works for the first run, then
    it listens forever". Every per-response flag has to be back at rest after
    each boundary, and — the load-bearing part — the microphone has to still
    reach the transport, because on this provider a muted microphone also
    starves the only source of the next turn's speech edges.
    """
    provider = SubscriptionLikeProvider([])
    jsons = []
    sess = _half_duplex_session(provider, jsons=jsons)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    for index in range(3):
        await session.queue.put(RealtimeEvent(type="speech_started"))
        await session.queue.put(
            RealtimeEvent(
                type="input_transcript",
                text=f"question number {index}",
                is_final=True,
            )
        )
        await session.queue.put(
            RealtimeEvent(type="output_transcript_delta", text="Here you go.")
        )
        await session.queue.put(
            RealtimeEvent(type="audio_delta", audio=_pcm_chunk())
        )
        await session.queue.put(RealtimeEvent(type="turn_complete"))
        await _wait_until(
            lambda i=index: len(
                [m for m in jsons if m.get("type") == "turn_complete"]
            )
            > i
        )
        # The boundary settles across two awaits: the surface message goes out
        # first, then the record is published, and only then is the duplex
        # state reset (the record reads _output_samples_sent, so it cannot be
        # reordered). Let it settle rather than racing it.
        try:
            await _wait_until(lambda: sess._output_active is False)
        except TimeoutError:
            pass  # fall through to the explicit assertion below
        assert sess._output_active is False, f"turn {index + 1} stayed speaking"
        assert sess._output_samples_sent == 0
        assert sess._response_requested_for_turn is False
        assert sess._user_speech_active is False
        assert sess._transport_rebuild_pending is None
        assert await _microphone_reaches(sess, session), (
            f"the microphone was still muted after turn {index + 1}"
        )

    # The transport declares rebuild_on_transport_death, so ENDING the
    # stream would only make the pump reopen it. End the call instead.
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_scrub_held_provider_audio_cannot_deafen_the_follow_up():
    """Only audio that reaches the speaker may engage half-duplex.

    ChatGPT-Live can resume an old response while its transcript is still
    missing. The scrub gate correctly holds that audio, but the session used
    to mark itself as speaking before the hold. Every microphone frame was
    then discarded even though the desktop already displayed LISTENING: the
    first turn worked and the second question vanished.
    """
    provider = SubscriptionLikeProvider([])
    binaries = []
    sess = _half_duplex_session(provider, binaries=binaries)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    await session.queue.put(
        RealtimeEvent(
            type="audio_delta",
            audio=_pcm_chunk(),
            provider_turn_id="late-response",
        )
    )
    await _wait_until(lambda: sess._gate.pending_audio_ms > 0)  # noqa: SLF001

    assert binaries == []
    assert sess._output_active is False  # noqa: SLF001
    assert await _microphone_reaches(sess, session), (
        "scrub-held provider audio muted the second user turn"
    )

    # A real second utterance retires the held stale response. The next response
    # is allowed to speak, and only that cleared PCM engages half-duplex.
    session.isolates_response_generations = True
    await session.queue.put(RealtimeEvent(type="speech_started"))
    await session.queue.put(
        RealtimeEvent(
            type="input_transcript",
            text="What day is tomorrow?",
            is_final=True,
        )
    )
    await session.queue.put(
        RealtimeEvent(
            type="output_transcript_delta",
            text="This is the complete answer to your question.",
            is_final=True,
            provider_turn_id="fresh-response",
        )
    )
    fresh_audio = AudioChunk(
        pcm=b"\x20\x30" * 240,
        sample_rate=24_000,
        timestamp_ns=0,
    )
    await session.queue.put(
        RealtimeEvent(
            type="audio_delta",
            audio=fresh_audio,
            provider_turn_id="fresh-response",
        )
    )
    await _wait_until(lambda: bool(binaries))
    assert binaries == [fresh_audio.pcm]
    assert sess._output_active is True  # noqa: SLF001
    assert not await _microphone_reaches(sess, session)

    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_a_boundary_after_a_local_completion_still_reopens_the_microphone():
    """The turn-id guard used to swallow the whole reset.

    ``_complete_surface_turn`` returned early when an earlier local path had
    already cleared the turn id, so ``_output_active`` — the flag half-duplex
    mutes the microphone on — was never cleared. On this transport that is
    terminal: the muted microphone starves the local endpointer, so no speech
    edge, no transcript and no further boundary can ever arrive to clear it.
    """
    provider = SubscriptionLikeProvider([])
    sess = _half_duplex_session(provider)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    await session.queue.put(
        RealtimeEvent(type="input_transcript", text="hello", is_final=True)
    )
    await session.queue.put(
        RealtimeEvent(
            type="output_transcript_delta",
            text="This is the complete answer to your question.",
            is_final=True,
        )
    )
    await session.queue.put(
        RealtimeEvent(type="audio_delta", audio=_pcm_chunk())
    )
    await _wait_until(lambda: sess._output_active)

    # A local path closes the turn without touching the duplex flags — this is
    # what _begin_user_speech_turn and the old decline path both did.
    await sess._publish_turn_completed()
    assert sess._turn_id == ""
    assert sess._output_active is True
    assert not await _microphone_reaches(sess, session)

    await session.queue.put(RealtimeEvent(type="turn_complete"))
    await _wait_until(lambda: sess._output_active is False)
    assert await _microphone_reaches(sess, session)

    # The transport declares rebuild_on_transport_death, so ENDING the
    # stream would only make the pump reopen it. End the call instead.
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_a_silent_provider_turn_is_closed_locally_and_said_out_loud(
    monkeypatch,
):
    """A transport that stops emitting must not hold the call open forever.

    The receive iterator has no timeout and neither does the surface's
    wait_finished(), so an adapter that latches emits nothing at all — no
    audio, no transcript, no boundary, no error — and the call sits with the
    microphone shut until the user kills it. The watchdog is the independent
    backstop: it never trusts the transport to report its own death.
    """
    import jarvis.realtime.session as session_module

    monkeypatch.setattr(session_module, "_TURN_STALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(session_module, "_TURN_STALL_POLL_S", 0.02)

    provider = SubscriptionLikeProvider([])
    jsons = []
    sess = _half_duplex_session(provider, jsons=jsons)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    await session.queue.put(
        RealtimeEvent(
            type="input_transcript", text="what time is it", is_final=True
        )
    )
    await _wait_until(lambda: sess._turn_id != "")
    # ...and now the transport simply goes quiet.

    await _wait_until(
        lambda: any(m.get("type") == "turn_complete" for m in jsons)
    )
    spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    assert spoken, "a stalled turn must be reported out loud, never silently"
    assert spoken[0]["text"].strip(), "the notice must carry real words"
    assert sess._output_active is False
    assert sess._response_requested_for_turn is False
    assert sess._drop_provider_output_until_new_response is False
    assert await _microphone_reaches(sess, session)

    # The transport declares rebuild_on_transport_death, so ENDING the
    # stream would only make the pump reopen it. End the call instead.
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_the_stall_watchdog_never_fires_between_turns(monkeypatch):
    """AP-19 / BUG-032: a watchdog that outlives its unit of work aborts the
    NEXT one. This one is armed per turn and cancelled at every boundary, so
    an idle session between turns must stay completely quiet."""
    import jarvis.realtime.session as session_module

    monkeypatch.setattr(session_module, "_TURN_STALL_TIMEOUT_S", 0.15)
    monkeypatch.setattr(session_module, "_TURN_STALL_POLL_S", 0.02)

    provider = SubscriptionLikeProvider([])
    jsons = []
    sess = _half_duplex_session(provider, jsons=jsons)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    await session.queue.put(
        RealtimeEvent(type="input_transcript", text="hello", is_final=True)
    )
    await session.queue.put(
        RealtimeEvent(
            type="output_transcript_delta",
            text="This is the complete answer to your question.",
            is_final=True,
        )
    )
    await session.queue.put(
        RealtimeEvent(type="audio_delta", audio=_pcm_chunk())
    )
    await session.queue.put(RealtimeEvent(type="turn_complete"))
    await _wait_until(
        lambda: any(m.get("type") == "turn_complete" for m in jsons)
    )

    # Idle for many watchdog windows with no turn open.
    await asyncio.sleep(0.6)
    assert sess._turn_stall_task is None or sess._turn_stall_task.done()
    assert not [m for m in jsons if m.get("type") == "error_spoken"], (
        "the watchdog fired between turns"
    )

    # The transport declares rebuild_on_transport_death, so ENDING the
    # stream would only make the pump reopen it. End the call instead.
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_a_discarded_rebuild_request_releases_the_microphone_marker():
    """The pending-rebuild marker gates handle_audio_frame all by itself.

    Only ``_rebuild_transport`` cleared it — precisely the path a discarded
    request skips — so a request that lost its race left every later
    microphone frame silently dropped for the rest of the call.
    """
    provider = SubscriptionLikeProvider([])
    sess = _half_duplex_session(provider)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    # A queued advised rebuild that the pump will refuse (the session is no
    # longer rebuildable by the time it is dequeued).
    sess._transport_rebuild_pending = session
    sess._failed.set()
    sess._transport_rebuild_requests.put_nowait((session, "advised reconnect"))

    await _wait_until(lambda: sess._transport_rebuild_pending is None)
    assert await _microphone_reaches(sess, session)

    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_a_handoff_declined_through_provider_speech_closes_its_turn():
    """Both decline branches must leave the same state behind.

    The provider-speech branch returned without a boundary, and the withhold
    armed by the user's own speech edge then made _emit_audio drop the refusal
    audio too — so the user heard nothing and the turn stayed open with
    _output_active standing.
    """
    provider = SubscriptionLikeProvider([])
    jsons = []
    # brain=None => no deterministic delegate, and this transport cannot
    # declare tools either, so the handoff has nowhere to go.
    sess = _half_duplex_session(provider, jsons=jsons)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    await session.queue.put(RealtimeEvent(type="speech_started"))
    await session.queue.put(
        RealtimeEvent(
            type="handoff_requested",
            text="Open the settings view.",
            handoff_id="handoff-1",
        )
    )
    await _wait_until(lambda: bool(session.spoken))
    assert session.spoken, "the refusal must be voiced by the provider"
    await _wait_until(
        lambda: any(m.get("type") == "turn_complete" for m in jsons)
    )

    assert sess._drop_provider_output_until_new_response is False, (
        "the refusal is our own scrubbed text; the model withhold must not "
        "swallow it"
    )
    assert sess._output_active is False
    assert sess._response_requested_for_turn is False
    assert await _microphone_reaches(sess, session)

    # The transport declares rebuild_on_transport_death, so ENDING the
    # stream would only make the pump reopen it. End the call instead.
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_grounded_final_releases_barge_in_withhold_for_automatic_transport():
    """An automatic response may already exist when the local final arrives.

    Live 2026-08-10: the Codex subscription opened its replacement response
    before local transcription completed.  ``_response_requested_for_turn``
    therefore skipped the manual request branch that used to be the only place
    clearing the barge-in guard; three complete replies were discarded and the
    first audible frame arrived 23.3 seconds into the session.
    """
    provider = SubscriptionLikeProvider([])
    binaries = []
    sess = _half_duplex_session(provider, binaries=binaries)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    sess._drop_provider_output_until_new_response = True  # noqa: SLF001
    sess._response_requested_for_turn = True  # noqa: SLF001
    await session.queue.put(
        RealtimeEvent(
            type="input_transcript",
            text="Hello there.",
            is_final=True,
        )
    )
    await _wait_until(lambda: sess._last_user_text == "Hello there.")  # noqa: SLF001
    await _wait_until(  # noqa: SLF001
        lambda: sess._drop_provider_output_until_new_response is False
    )
    await session.queue.put(
        RealtimeEvent(
            type="output_transcript_delta",
            text="This is the complete answer to your question.",
            is_final=True,
            provider_turn_id="replacement-response",
        )
    )
    fresh_audio = _pcm_chunk()
    await session.queue.put(
        RealtimeEvent(
            type="audio_delta",
            audio=fresh_audio,
            provider_turn_id="replacement-response",
        )
    )

    await _wait_until(lambda: bool(binaries))
    assert binaries == [fresh_audio.pcm]
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_a_transport_rebuild_retires_the_answered_input_ids():
    """Input-item ids belong to the transport that issued them.

    A fresh provider session may restart its numbering, and a collision makes
    the duplicate-input guard swallow the next real utterance: the user speaks
    and no turn ever opens.
    """
    provider = SubscriptionLikeProvider([])
    sess = _half_duplex_session(provider)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    first = provider.session
    sess._response_requested_input_ids.add("item_0")

    assert await sess._rebuild_transport(detail="test rebuild") is True

    assert provider.session is not first
    assert sess._response_requested_input_ids == set()
    assert sess._transport_rebuild_pending is None
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_the_surface_is_told_the_call_language_and_the_start_budget():
    """The UI had no way to learn either.

    ``VoiceSessionStarted`` carries the language but is published for the
    browser surface only; ``audio_ready`` carried none at all, and a cold
    subscription handshake was pure dead air with no declared budget to show.
    One producer, one field name, one value.
    """
    provider = SubscriptionLikeProvider([])
    jsons = []
    sess = _half_duplex_session(provider, jsons=jsons)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    starting = next(m for m in jsons if m.get("type") == "audio_starting")
    ready = next(m for m in jsons if m.get("type") == "audio_ready")
    announced = next(m for m in jsons if m.get("type") == "language")

    assert ready["language"] == sess._language
    assert announced["language"] == sess._language
    assert starting["language"] == sess._language
    # The interim notice precedes the handshake it is announcing.
    assert jsons.index(starting) < jsons.index(ready)
    assert starting["handshake_budget_s"] >= provider.handshake_budget_s

    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_a_self_initiated_interruption_is_not_read_as_user_speech():
    """Jarvis's own interrupt() echoing back must not arm the user-speech state.

    Every site that issues one already drained the gate and armed its own
    withhold; treating the echo as a barge-in blocked announcements, late
    action results and the readback watchdog against a user who never spoke.
    """
    provider = SubscriptionLikeProvider([])
    sess = _half_duplex_session(provider)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    session = provider.session

    await session.queue.put(
        RealtimeEvent(type="interrupted", self_initiated=True)
    )
    await session.queue.put(
        RealtimeEvent(type="input_transcript", text="marker", is_final=True)
    )
    await _wait_until(lambda: sess._last_user_text == "marker")

    assert sess._user_speech_active is False
    assert session.interrupts == 0, (
        "a self-initiated interruption must not be answered with another one"
    )

    # The transport declares rebuild_on_transport_death, so ENDING the
    # stream would only make the pump reopen it. End the call instead.
    await sess.end(reason="test")


class _UnreachableProvider(SubscriptionLikeProvider):
    """A provider whose handshake never succeeds."""

    def __init__(self, error):
        super().__init__([])
        self._error = error

    async def open_session(self, cfg):
        raise self._error


def _no_metered_fallback_session(provider, *, jsons=None, language_cfg=None):
    """A subscription-shaped session that refuses usage-billed fallback."""
    cfg = _delegate_cfg("delegate")
    if language_cfg is not None:
        cfg.brain.reply_language = language_cfg
    return RealtimeVoiceSession(
        session_id="handshake-test",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=(
            (lambda m: jsons.append(m) or asyncio.sleep(0))
            if jsons is not None
            else (lambda _m: asyncio.sleep(0))
        ),
        provider=provider,
        config=cfg,
        half_duplex=True,
        surface="desktop",
        allow_classic_fallback=False,
    )


@pytest.mark.asyncio
async def test_an_exhausted_handshake_budget_says_why_before_it_gives_up():
    """ST-8: the call used to end after the full budget in total silence.

    A subscription transport declares a long handshake budget and refuses to
    cross into usage-billed voice. Both are correct. What was missing is the
    ending: the surface turns the handshake failure into reason=error, so the
    user waited up to 45 s and then the call simply stopped with nothing said.
    """
    provider = _UnreachableProvider(
        TimeoutError("realtime handshake exceeded 45.0s provider budget")
    )
    jsons = []
    sess = _no_metered_fallback_session(provider, jsons=jsons)

    with pytest.raises(RuntimeError):
        await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    assert spoken, "the user must be told why the call is ending"
    notice = spoken[0]
    assert notice["text"].strip(), "the notice must carry real words"
    assert notice["language"] == sess._language
    # The desktop surface resolves its realtime-scoped TTS from the provider;
    # on a failed handshake its ambient copy was never set, so the payload has
    # to name it or the notice stays text-only (mode separation stays intact).
    assert notice["provider"] == provider.name
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_the_handshake_notice_names_the_cause_it_actually_hit():
    """Cause-aware, not one generic apology.

    "It did not come up in time" and "it could not be reached" send the user
    to different places, so the two must not collapse into the same sentence.
    """
    timeout_jsons = []
    timeout_sess = _no_metered_fallback_session(
        _UnreachableProvider(
            TimeoutError("realtime handshake exceeded 45.0s provider budget")
        ),
        jsons=timeout_jsons,
    )
    with pytest.raises(RuntimeError):
        await timeout_sess.handle_control(
            {"type": "audio_start", "sample_rate": 16_000}
        )

    other_jsons = []
    other_sess = _no_metered_fallback_session(
        _UnreachableProvider(RuntimeError("the dedicated profile is logged out")),
        jsons=other_jsons,
    )
    with pytest.raises(RuntimeError):
        await other_sess.handle_control(
            {"type": "audio_start", "sample_rate": 16_000}
        )

    timeout_text = next(
        m for m in timeout_jsons if m.get("type") == "error_spoken"
    )["text"]
    other_text = next(
        m for m in other_jsons if m.get("type") == "error_spoken"
    )["text"]
    assert timeout_text != other_text, (
        "a timeout and an unreachable engine must not read identically"
    )
    await timeout_sess.end(reason="test")
    await other_sess.end(reason="test")


@pytest.mark.asyncio
async def test_the_handshake_notice_follows_the_pinned_reply_language():
    """One resolver decides, here as everywhere (CLAUDE.md §1 runtime rule 1).

    Every supported locale is equal: a Spanish-pinned user must not be told in
    English (or German) that the call is ending.
    """
    seen = {}
    for pin in ("de", "en", "es"):
        jsons = []
        sess = _no_metered_fallback_session(
            _UnreachableProvider(
                TimeoutError("realtime handshake exceeded 45.0s provider budget")
            ),
            jsons=jsons,
            language_cfg=pin,
        )
        with pytest.raises(RuntimeError):
            await sess.handle_control(
                {"type": "audio_start", "sample_rate": 16_000}
            )
        notice = next(m for m in jsons if m.get("type") == "error_spoken")
        assert notice["language"] == pin
        seen[pin] = notice["text"]
        await sess.end(reason="test")

    assert len(set(seen.values())) == 3, (
        f"each supported locale needs its own wording, got {seen}"
    )


@pytest.mark.asyncio
async def test_a_recoverable_handshake_failure_stays_silent():
    """Silence is correct when the classic pipeline still answers the user.

    The notice exists because the call ENDS. When usage-billed fallback is
    permitted the pipeline picks the same call up and the user gets a normal
    answer; announcing a failure there would be false.
    """
    provider = _UnreachableProvider(
        TimeoutError("realtime handshake exceeded 45.0s provider budget")
    )
    jsons = []
    sess = _session(provider, jsons=jsons)  # allow_classic_fallback defaults True

    with pytest.raises(RuntimeError):
        await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})

    assert not [m for m in jsons if m.get("type") == "error_spoken"], (
        "the classic pipeline owns this call; nothing should be announced"
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_silent_provider_audio_does_not_advance_liveness_or_echo_horizon():
    """Embedded/trailing silence is forwarded to the player but must not be
    stamped as live output: silence cannot echo into the microphone, and
    dating the echo horizon forward for it held the half-duplex gate deaf for
    seconds after every reply (live 2026-08-04)."""
    from types import SimpleNamespace

    sess = RealtimeVoiceSession(
        session_id="s-silence-liveness",
        send_binary=lambda _pcm: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(),
        bus=None,
    )
    loud = SimpleNamespace(
        pcm=(1200).to_bytes(2, "little", signed=True) * 480, sample_rate=24_000
    )
    quiet = SimpleNamespace(pcm=b"\x00" * 960, sample_rate=24_000)

    await sess._emit_audio(loud)
    stamped_at = sess._last_output_audio_at
    horizon = sess._echo_playback_horizon
    assert stamped_at > 0.0
    assert horizon > 0.0

    await sess._emit_audio(quiet)
    assert sess._last_output_audio_at == stamped_at
    assert sess._echo_playback_horizon == horizon

    await sess._emit_audio(loud)
    assert sess._last_output_audio_at >= stamped_at
    assert sess._echo_playback_horizon > horizon
    await sess.end(reason="test")


async def _drive_speech_edge(sess) -> None:
    """The REAL pump sequence for a speech edge: begin + barge-in.

    Testing ``_begin_user_speech_turn`` alone is exactly how the first fix
    became a no-op — ``_barge_in`` runs one line later in production and
    used to re-arm the withhold unconditionally (independent review W1/W3).
    """
    await sess._begin_user_speech_turn()
    await sess._barge_in(interrupt_provider=True)


@pytest.mark.asyncio
async def test_speech_edge_without_a_playing_reply_does_not_withhold_audio():
    """A fresh utterance must not swallow the head of the answer it earns.

    On a transport whose local recognizer needs a network round trip, the
    server's answer regularly BEGINS before the final transcript lands; the
    unconditional edge withhold dropped its first seconds and playback
    entered mid-sentence (live 2026-08-05 20:12: 105 withheld audio events,
    the reply audible only from its middle).
    """
    sess = RealtimeVoiceSession(
        session_id="s-speech-edge",
        send_binary=lambda _pcm: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(),
        bus=None,
    )

    await _drive_speech_edge(sess)
    assert sess._drop_provider_output_until_new_response is False

    # With a reply audibly playing the same edge IS a barge-in: withhold.
    sess._output_active = True
    await _drive_speech_edge(sess)
    assert sess._drop_provider_output_until_new_response is True

    # And a requested-but-not-yet-audible response counts the same way.
    sess._output_active = False
    sess._drop_provider_output_until_new_response = False
    sess._response_requested_for_turn = True
    await _drive_speech_edge(sess)
    assert sess._drop_provider_output_until_new_response is True
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_speech_edge_keeps_the_untranscribed_answer_head_buffered():
    """The gate buffer must survive an edge with no reply to cut (review W2).

    The head of the incoming answer sits in the scrub gate awaiting its
    transcript; draining it on every speech edge deleted exactly those
    seconds even after the withhold itself was made conditional.
    """
    sess = RealtimeVoiceSession(
        session_id="s-speech-edge-gate",
        send_binary=lambda _pcm: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider([]),
        config=_cfg(),
        bus=None,
    )
    head = AudioChunk(pcm=b"\x00\x01" * 24_000, sample_rate=24_000, timestamp_ns=0)
    assert await sess._gate.push_audio(head) == []
    assert sess._gate.pending_audio_ms > 0

    await _drive_speech_edge(sess)
    assert sess._gate.pending_audio_ms > 0

    # A real barge-in still discards the interrupted reply's buffer.
    sess._output_active = True
    await _drive_speech_edge(sess)
    assert sess._gate.pending_audio_ms == 0
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_session_instructions_carry_the_one_speaker_discipline():
    """The one-speaker rule must travel on the channel the voice model reads.

    The equivalent rule in the Codex thread-start base instructions is
    demonstrably inert for the live voice: three calls in a row it performed
    BOTH sides of a greeting exchange and hung itself up (2026-08-05 20:42),
    while rules delivered via the session/developer-context channel (the ack
    ban, the language pin) were honored.
    """
    from jarvis.realtime.session import _session_instructions

    text = _session_instructions("de")
    assert "ONE voice" in text
    assert "Never speak both sides" in text
    assert "Do not say goodbye" in text


@pytest.mark.asyncio
async def test_shadow_transcript_clears_the_gate_without_reaching_the_surface():
    """A shadow delta is vetting material: audio releases, no transcript shows.

    The provider's own text follows later and would double up if the locally
    recovered shadow text were surfaced.
    """
    jsons = []
    binaries = []
    events = [
        RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(
                pcm=b"\x00\x01" * 2_400, sample_rate=24_000, timestamp_ns=0
            ),
        ),
        RealtimeEvent(
            type="output_transcript_delta",
            text="This is the complete answer to your question.",
            shadow=True,
        ),
    ]
    sess = RealtimeVoiceSession(
        session_id="s-shadow",
        send_binary=lambda pcm: binaries.append(pcm) or asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.sleep(0.1)

    assert binaries, "shadow-cleared audio must reach playback"
    surfaced = [
        message
        for message in jsons
        if message.get("type") == "transcript"
        and "Here is the answer." in str(message.get("text", ""))
    ]
    assert surfaced == [], "shadow text must never reach the surface"
    await sess.end(reason="test")


async def test_a_turn_without_a_boundary_releases_the_microphone_fast() -> None:
    """ChatGPT-Live announces no terminal item (probe-confirmed 2026-08-06):
    a turn whose backstop never fires used to hold the half-duplex mute six
    full seconds. The release now needs only ~2 s of provider silence - and a
    reply that is still streaming keeps its mute."""
    provider = SubscriptionLikeProvider([])
    sess = _half_duplex_session(provider)
    await sess.handle_control({"type": "audio_start", "sample_rate": 24_000})
    wire = provider.session

    # Cleared assistant audio opens the output: the microphone mutes. Raw PCM
    # that is still awaiting a transcript deliberately stays interruptible.
    wire.queue.put_nowait(
        RealtimeEvent(
            type="output_transcript_delta",
            text="This is the complete answer to your question.",
            is_final=True,
        )
    )
    wire.queue.put_nowait(RealtimeEvent(type="audio_delta", audio=_pcm_chunk()))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if sess._output_active:  # noqa: SLF001
            break
    assert sess._output_active is True  # noqa: SLF001

    # The provider goes silent WITHOUT any boundary. Keep feeding microphone
    # frames like the live pipeline does.
    mic_frame = b"\x11\x22" * 480
    released_after = None
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < 4.0:
        await sess.handle_audio_frame(mic_frame)
        await asyncio.sleep(0.05)
        if wire.sent_audio:
            released_after = asyncio.get_event_loop().time() - start
            break
    assert released_after is not None, "the microphone stayed deaf"
    assert released_after >= 1.5, (
        f"released after {released_after:.2f}s - a streaming reply must keep "
        "its mute for the full silent-release window"
    )
    assert released_after < 3.5, (
        f"released only after {released_after:.2f}s - the fast release "
        "should fire at ~2 s of provider silence"
    )
    await sess.end(reason="test")
