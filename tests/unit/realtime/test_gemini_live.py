"""Unit tests for the Gemini Live realtime adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.model_catalog import REALTIME_MODELS
from jarvis.plugins.realtime.gemini_live import (
    GeminiLiveProvider,
    VertexLiveProvider,
    _GeminiLiveSession,
)
from jarvis.realtime.protocol import RealtimeSessionConfig


def _fake_message(*, data=None, server_content=None, tool_call=None, go_away=None):
    return SimpleNamespace(
        data=data,
        server_content=server_content,
        tool_call=tool_call,
        go_away=go_away,
    )


@pytest.mark.asyncio
async def test_key_injection_controls_availability():
    assert await GeminiLiveProvider(api_key="test-key").can_open_duplex_session() is True
    assert await GeminiLiveProvider().can_open_duplex_session() is False


@pytest.mark.asyncio
async def test_receive_maps_audio_transcripts_interrupt_and_completion():
    messages = [
        _fake_message(data=b"\x01\x02\x03\x04"),
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=SimpleNamespace(text="hello there"),
                input_transcription=SimpleNamespace(text="what the user said"),
                interrupted=True,
                turn_complete=True,
            )
        ),
    ]

    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            for message in messages:
                yield message

    fake_session = SimpleNamespace(receive=fake_receive)
    session = _GeminiLiveSession(
        session=fake_session,
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s1",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "audio_delta",
        "output_transcript_delta",
        "interrupted",
        "input_transcript",
        "turn_complete",
    ]
    assert events[0].audio.pcm == b"\x01\x02\x03\x04"
    assert events[0].audio.sample_rate == 24_000
    assert events[1].text == "hello there"
    assert events[3].text == "what the user said"
    assert receive_calls == 2


@pytest.mark.asyncio
async def test_go_away_is_recoverable_and_keeps_the_stream_flowing() -> None:
    """GoAway is Gemini's courteous pre-disconnect notice, not a wire error.
    Surfacing it as terminal used to end the session with reason=error while
    the current reply was still being spoken (live incident 2026-07-15 17:40),
    dropping the buffered audio tail."""
    messages = [
        _fake_message(go_away=SimpleNamespace(time_left=3_000)),
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=SimpleNamespace(text="still speaking"),
                input_transcription=None,
                interrupted=False,
                turn_complete=True,
            )
        ),
    ]

    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            for message in messages:
                yield message

    fake_session = SimpleNamespace(receive=fake_receive)
    session = _GeminiLiveSession(
        session=fake_session,
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s-go-away",
    )

    events = [event async for event in session.receive()]

    errors = [event for event in events if event.type == "error"]
    assert len(errors) == 1
    assert errors[0].recoverable is True
    # The notice also advises a proactive rebuild: a client that merely
    # keeps using the socket is hard-killed with 1008 when the announced
    # window expires (live 2026-07-21 11:14).
    assert errors[0].reconnect_advised is True
    assert "reconnect" in (errors[0].error or "")
    # The notice must not terminate the stream: the reply that follows it is
    # still delivered.
    assert [event.type for event in events][-2:] == [
        "output_transcript_delta",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_abnormal_turn_complete_reason_is_logged(caplog) -> None:
    """Every named TurnCompleteReason except UNSPECIFIED is an abnormal stop
    (safety filter, rejection, regeneration limit). Discarding it made a
    server-truncated spoken reply indistinguishable from a complete one."""
    messages = [
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=SimpleNamespace(text="partial answer"),
                input_transcription=None,
                interrupted=False,
                turn_complete=True,
                turn_complete_reason=SimpleNamespace(
                    name="MAX_REGENERATION_REACHED"
                ),
            )
        ),
    ]

    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            for message in messages:
                yield message

    fake_session = SimpleNamespace(receive=fake_receive)
    session = _GeminiLiveSession(
        session=fake_session,
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s-reason",
    )

    with caplog.at_level("WARNING"):
        events = [event async for event in session.receive()]

    assert [event.type for event in events][-1] == "turn_complete"
    assert any(
        "MAX_REGENERATION_REACHED" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_receive_reenters_sdk_iterator_for_a_second_user_turn() -> None:
    sdk_turns = [
        [
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="first answer"),
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            )
        ],
        [
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="second answer"),
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            )
        ],
    ]
    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        turn_index = receive_calls - 1
        if turn_index < len(sdk_turns):
            for message in sdk_turns[turn_index]:
                yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="two-turns",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "output_transcript_delta",
        "turn_complete",
        "output_transcript_delta",
        "turn_complete",
    ]
    assert [event.text for event in events if event.text] == [
        "first answer",
        "second answer",
    ]
    assert receive_calls == 3


@pytest.mark.asyncio
async def test_text_update_uses_realtime_input_for_gemini_31() -> None:
    calls: list[dict[str, str]] = []

    async def send_realtime_input(**kwargs):
        calls.append(kwargs)

    session = _GeminiLiveSession(
        session=SimpleNamespace(send_realtime_input=send_realtime_input),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s-text",
    )

    await session.send_text("Deliver the completed mission update.")

    assert calls == [{"text": "Deliver the completed mission update."}]


_TURN_DIRECTIVE = "Delegate this turn: call jarvis_action before you answer."
_STANDING_DIRECTIVE = "Live-call discipline: you are ONE voice."


def _user_speaking_message(text="was ist die uhrzeit", turn_complete=False):
    """One server message that puts the user mid-turn (no model output yet)."""
    return _fake_message(
        server_content=SimpleNamespace(
            output_transcription=None,
            input_transcription=SimpleNamespace(text=text),
            interrupted=False,
            turn_complete=turn_complete,
        )
    )


def _model_speaking_message(text="hier ist die antwort"):
    return _fake_message(
        server_content=SimpleNamespace(
            output_transcription=SimpleNamespace(text=text),
            input_transcription=None,
            interrupted=False,
            turn_complete=False,
        )
    )


def _turn_complete_message():
    return _fake_message(
        server_content=SimpleNamespace(
            output_transcription=None,
            input_transcription=None,
            interrupted=False,
            turn_complete=True,
        )
    )


def _steering_session(sent, *, instructions="", language="", session_id="s-steer"):
    """A session whose realtime-input sends are captured, driven by messages."""
    queue: list[list[object]] = []

    async def fake_receive():
        if queue:
            for message in queue.pop(0):
                yield message

    async def send_realtime_input(**kwargs):
        sent.append(kwargs)

    session = _GeminiLiveSession(
        session=SimpleNamespace(
            receive=fake_receive, send_realtime_input=send_realtime_input
        ),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id=session_id,
        instructions=instructions,
        language=language,
    )
    return session, queue


async def _drive(session, queue, messages):
    queue.append(list(messages))
    return [event async for event in session.receive()]


@pytest.mark.asyncio
async def test_changed_turn_directive_reaches_the_model_as_a_text_turn() -> None:
    sent: list[dict[str, str]] = []
    session, queue = _steering_session(sent, instructions="fixed prompt")

    await _drive(session, queue, [_user_speaking_message()])
    await session.update_session(
        instructions="x" * 21_000, turn_directive=_TURN_DIRECTIVE
    )

    assert len(sent) == 1
    text = sent[0]["text"]
    assert _TURN_DIRECTIVE in text
    # A developer message, not a new user request — but the user's last
    # utterance must still be answered (live 2026-08-19: "do not answer it"
    # closed every greeting as silence).
    assert "silent configuration" in text
    assert "answer THEM now" in text
    # The rebuilt instruction block never travels; only the delta does.
    assert "x" * 100 not in text
    assert len(text) < 1_000


@pytest.mark.asyncio
async def test_unchanged_directive_sends_nothing_on_the_next_turn() -> None:
    sent: list[dict[str, str]] = []
    session, queue = _steering_session(sent, instructions="fixed prompt")

    await _drive(session, queue, [_user_speaking_message()])
    await session.update_session(turn_directive=_TURN_DIRECTIVE)
    assert len(sent) == 1

    # Second turn, same directive: the model already has it.
    await _drive(session, queue, [_user_speaking_message("und morgen")])
    await session.update_session(turn_directive=_TURN_DIRECTIVE)
    assert len(sent) == 1

    # Third turn with a DIFFERENT directive travels again.
    await _drive(session, queue, [_user_speaking_message("mach es")])
    await session.update_session(turn_directive="Answer directly this turn.")
    assert len(sent) == 2
    assert "Answer directly this turn." in sent[1]["text"]


@pytest.mark.asyncio
async def test_directive_already_in_the_fixed_instructions_sends_nothing() -> None:
    sent: list[dict[str, str]] = []
    session, queue = _steering_session(
        sent,
        instructions=f"You are Jarvis.\n\n{_STANDING_DIRECTIVE}\n\n{_TURN_DIRECTIVE}",
    )

    await _drive(session, queue, [_user_speaking_message()])
    await session.update_session(
        turn_directive=_TURN_DIRECTIVE, standing_directive=_STANDING_DIRECTIVE
    )

    assert sent == []


@pytest.mark.asyncio
async def test_language_pin_travels_only_when_it_changes() -> None:
    sent: list[dict[str, str]] = []
    session, queue = _steering_session(sent, instructions="fixed", language="de")

    await _drive(session, queue, [_user_speaking_message()])
    await session.update_session(language="de")
    assert sent == []

    await _drive(session, queue, [_user_speaking_message("switch please")])
    await session.update_session(language="en")
    assert len(sent) == 1
    assert "en" in sent[0]["text"]


@pytest.mark.asyncio
async def test_steering_waits_while_the_model_is_generating() -> None:
    sent: list[dict[str, str]] = []
    session, queue = _steering_session(sent, instructions="fixed")

    # The model is mid-reply: a text input here would interrupt it.
    await _drive(session, queue, [_model_speaking_message()])
    await session.update_session(turn_directive=_TURN_DIRECTIVE)
    assert sent == []

    # The finished model turn alone is not the window either — between two
    # turns a text input would open one of its own.
    await _drive(session, queue, [_turn_complete_message()])
    assert sent == []

    # The next user utterance IS the window, and the delta is delivered
    # BEFORE the transcript reaches the orchestrator.
    events = await _drive(session, queue, [_user_speaking_message()])
    assert [event.type for event in events] == ["input_transcript"]
    assert len(sent) == 1
    assert _TURN_DIRECTIVE in sent[0]["text"]


@pytest.mark.asyncio
async def test_newer_directive_supersedes_an_undelivered_one() -> None:
    sent: list[dict[str, str]] = []
    session, queue = _steering_session(sent, instructions="fixed")

    await _drive(
        session, queue, [_model_speaking_message(), _turn_complete_message()]
    )
    await session.update_session(turn_directive=_TURN_DIRECTIVE)
    await session.update_session(turn_directive="Answer directly this turn.")
    assert sent == []

    await _drive(session, queue, [_user_speaking_message()])
    assert len(sent) == 1
    assert "Answer directly this turn." in sent[0]["text"]
    assert _TURN_DIRECTIVE not in sent[0]["text"]


@pytest.mark.asyncio
async def test_a_failed_steering_send_keeps_the_delta_for_the_next_turn() -> None:
    sent: list[dict[str, str]] = []
    failures = {"count": 1}

    async def send_realtime_input(**kwargs):
        if failures["count"] > 0:
            failures["count"] -= 1
            raise RuntimeError("socket hiccup")
        sent.append(kwargs)

    queue: list[list[object]] = []

    async def fake_receive():
        if queue:
            for message in queue.pop(0):
                yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(
            receive=fake_receive, send_realtime_input=send_realtime_input
        ),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s-retry",
        instructions="fixed",
    )

    await _drive(session, queue, [_user_speaking_message()])
    await session.update_session(turn_directive=_TURN_DIRECTIVE)
    assert sent == []

    await _drive(session, queue, [_user_speaking_message("noch mal")])
    assert len(sent) == 1
    assert _TURN_DIRECTIVE in sent[0]["text"]


@pytest.mark.asyncio
async def test_barge_in_reopens_the_steering_window() -> None:
    sent: list[dict[str, str]] = []
    session, queue = _steering_session(sent, instructions="fixed")

    await session.update_session(turn_directive=_TURN_DIRECTIVE)
    assert sent == []

    # Model output, then the user cutting in inside ONE server message.
    await _drive(
        session,
        queue,
        [
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="ich erklaere"),
                    input_transcription=SimpleNamespace(text="stop"),
                    interrupted=True,
                    turn_complete=False,
                )
            )
        ],
    )

    assert len(sent) == 1
    assert _TURN_DIRECTIVE in sent[0]["text"]


@pytest.mark.asyncio
async def test_each_steering_key_is_tracked_on_its_own() -> None:
    sent: list[dict[str, str]] = []
    session, queue = _steering_session(sent, instructions="fixed", language="de")

    await _drive(session, queue, [_user_speaking_message()])
    await session.update_session(
        turn_directive=_TURN_DIRECTIVE, standing_directive=_STANDING_DIRECTIVE
    )
    assert len(sent) == 1
    assert _TURN_DIRECTIVE in sent[0]["text"]
    assert _STANDING_DIRECTIVE in sent[0]["text"]

    # Only the turn directive moves: the standing one is already told, so it
    # must NOT ride along again on a channel that re-bills its whole context.
    await _drive(session, queue, [_user_speaking_message("und jetzt")])
    await session.update_session(
        turn_directive="Answer directly this turn.",
        standing_directive=_STANDING_DIRECTIVE,
    )
    assert len(sent) == 2
    assert "Answer directly this turn." in sent[1]["text"]
    assert _STANDING_DIRECTIVE not in sent[1]["text"]


def test_prompted_response_retry_is_advertised() -> None:
    # request_response() is a no-op on this transport, so without the
    # capability an answer blocked at the speech boundary is never replaced.
    assert _GeminiLiveSession.supports_prompted_response_retry is True
    # The capability is a PAIR: the orchestrator's retry path reads the flag
    # and then calls send_text, and raises "advertises prompted retries
    # without send_text" if the adapter has none. Advertising one without the
    # other turns a recoverable blocked answer into a failed retry.
    assert callable(_GeminiLiveSession.send_text)


class _FakeConnectCM:
    def __init__(self) -> None:
        self.exited = False

    async def __aenter__(self):
        return SimpleNamespace(name="fake-live-session")

    async def __aexit__(self, *_args):
        self.exited = True


class _FakeLiveAPI:
    def __init__(self) -> None:
        self.connect_calls: list[tuple[str, object]] = []
        self.last_cm: _FakeConnectCM | None = None

    def connect(self, *, model, config):
        self.connect_calls.append((model, config))
        self.last_cm = _FakeConnectCM()
        return self.last_cm


class _FakeAio:
    def __init__(self) -> None:
        self.live = _FakeLiveAPI()


class _FakeGenaiClient:
    def __init__(self, *, api_key=None) -> None:
        self.api_key = api_key
        self.aio = _FakeAio()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _patch_genai_client(monkeypatch: pytest.MonkeyPatch) -> dict:
    holder: dict = {}

    def _make_client(*, api_key=None, **_transport):  # http_options: shared TLS
        client = _FakeGenaiClient(api_key=api_key)
        holder["client"] = client
        return client

    from google import genai

    monkeypatch.setattr(genai, "Client", _make_client)
    return holder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [model.id for model in REALTIME_MODELS["gemini-live"]],
)
async def test_every_selectable_model_uses_live_audio_and_transcriptions(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    holder = _patch_genai_client(monkeypatch)
    provider = GeminiLiveProvider(api_key="test-key")

    session = await provider.open_session(
        RealtimeSessionConfig(
            model=model,
            voice="Puck",
            silence_duration_ms=2_700,
        )
    )

    selected, config = holder["client"].aio.live.connect_calls[0]
    assert selected == model
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None
    assert (
        config.realtime_input_config.automatic_activity_detection.silence_duration_ms
        == 2_700
    )
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"
    await session.close()
    assert holder["client"].closed is True


@pytest.mark.asyncio
async def test_thinking_pause_becomes_geminis_silence_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user's Thinking pause is the ONE lever on a self-answering transport.

    Gemini answers on its own activity boundary — Jarvis cannot hold a reply
    back once the turn is closed — so "wait for a clear pause before you take
    the turn" (maintainer 2026-08-18) can only be Gemini's own silence window.
    A user who resumes inside it continues the same activity: the words
    append, nothing is submitted twice. LOW sensitivity travels alongside it —
    the session sends both or neither, because the window without the patience
    is the wait with none of the protection it was added for.
    """
    from google.genai import types

    holder = _patch_genai_client(monkeypatch)
    provider = GeminiLiveProvider(api_key="test-key")

    session = await provider.open_session(
        RealtimeSessionConfig(
            voice="Puck", turn_pause_ms=1_500, end_of_speech_sensitivity="low"
        )
    )
    _selected, config = holder["client"].aio.live.connect_calls[0]
    detection = config.realtime_input_config.automatic_activity_detection
    assert detection.silence_duration_ms == 1_500
    assert detection.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_LOW
    assert detection.disabled is False
    await session.close()


@pytest.mark.asyncio
async def test_explicit_silence_window_outranks_the_thinking_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    provider = GeminiLiveProvider(api_key="test-key")

    session = await provider.open_session(
        RealtimeSessionConfig(
            voice="Puck", silence_duration_ms=2_700, turn_pause_ms=1_500
        )
    )
    _selected, config = holder["client"].aio.live.connect_calls[0]
    detection = config.realtime_input_config.automatic_activity_detection
    assert detection.silence_duration_ms == 2_700
    await session.close()


@pytest.mark.asyncio
async def test_default_config_sends_no_turn_detection_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default session leaves Gemini's factory turn detection alone.

    Nothing is sent — no window, no sensitivity, no activity-detection block
    — so the turn ends exactly when Gemini decides it does, which is the
    latency a caller gets straight from the vendor's API. A 1.5 s window
    layered on top of native endpointing made every finished sentence wait
    audibly longer than the vendor's own client (maintainer 2026-08-23); the
    patience of 2026-08-13 is now an explicit opt-in, not the default.
    """
    holder = _patch_genai_client(monkeypatch)
    provider = GeminiLiveProvider(api_key="test-key")

    session = await provider.open_session(RealtimeSessionConfig(voice="Puck"))
    _selected, config = holder["client"].aio.live.connect_calls[0]
    assert config.realtime_input_config is None
    await session.close()


@pytest.mark.asyncio
async def test_sensitivity_can_be_waived_back_to_the_provider_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    provider = GeminiLiveProvider(api_key="test-key")

    session = await provider.open_session(
        RealtimeSessionConfig(voice="Puck", end_of_speech_sensitivity=None)
    )
    _selected, config = holder["client"].aio.live.connect_calls[0]
    assert config.realtime_input_config is None
    await session.close()


@pytest.mark.asyncio
async def test_an_sdk_without_the_enum_still_opens_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AP-21: a capability gap degrades the session, never blocks it."""
    from google.genai import types

    holder = _patch_genai_client(monkeypatch)
    monkeypatch.delattr(types, "EndSensitivity", raising=False)
    provider = GeminiLiveProvider(api_key="test-key")

    session = await provider.open_session(RealtimeSessionConfig(voice="Puck"))
    _selected, config = holder["client"].aio.live.connect_calls[0]
    assert config.realtime_input_config is None
    assert config.speech_config is not None
    await session.close()


@pytest.mark.asyncio
async def test_open_session_pins_the_adapter_default_voice_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-155: an empty card pin used to omit voice_config, so the Live
    socket used Google's undocumented default while surface TTS spoke
    Charon. The adapter default is always sent so both paths share it."""
    holder = _patch_genai_client(monkeypatch)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    _selected, config = holder["client"].aio.live.connect_calls[0]
    assert (
        config.speech_config.voice_config.prebuilt_voice_config.voice_name
        == GeminiLiveProvider.default_voice
        == "Kore"
    )
    await session.close()


@pytest.mark.asyncio
async def test_open_session_uses_current_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(model="")
    )
    assert holder["client"].aio.live.connect_calls[0][0] == (
        "gemini-3.1-flash-live-preview"
    )
    # The session tells the orchestrator which id it REALLY opened — usage is
    # metered and priced against it, so an empty pin must not leak through.
    assert session.model == "gemini-3.1-flash-live-preview"
    await session.close()


@pytest.mark.asyncio
async def test_explicit_reply_language_uses_the_session_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(
            language="es",
            language_is_pinned=True,
            instructions="Reply only in Spanish for this turn.",
        )
    )
    _model, config = holder["client"].aio.live.connect_calls[0]

    assert config.system_instruction == "Reply only in Spanish for this turn."
    # An unpinned card still gets an EXPLICIT voice. This used to assert
    # ``speech_config is None``, which is the state BUG-155 was about: Google's
    # unpinned native-audio default is undocumented, and leaving the field
    # empty made every progress and fallback line speak Charon while the
    # session spoke something else.
    prebuilt = config.speech_config.voice_config.prebuilt_voice_config
    assert prebuilt.voice_name == GeminiLiveProvider.default_voice
    # Gemini auto-responds; the required-tool hint remains a compatible no-op.
    await session.request_response(required_tool="jarvis_action")
    await session.close()


@pytest.mark.asyncio
async def test_tools_are_declared_mapped_and_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    declaration = {
        "name": "open_app",
        "description": "Open an application.",
        "parameters": {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
        },
    }
    provider_session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(tools=(declaration,))
    )
    _model, config = holder["client"].aio.live.connect_calls[0]
    dumped = config.model_dump(exclude_none=True)
    assert dumped["tools"][0]["function_declarations"][0]["name"] == "open_app"
    # Gemini fixes tool declarations at connection time; accepting a live
    # update remains a safe no-op until the next session reconnects.
    await provider_session.update_session(tools=(declaration,))

    class FakeLiveSession:
        def __init__(self):
            self.responses = []

        async def receive(self):
            yield _fake_message(
                tool_call=SimpleNamespace(
                    function_calls=[
                        SimpleNamespace(
                            id="call-1",
                            name="open_app",
                            args={"app_name": "Calculator"},
                        )
                    ]
                )
            )

        async def send_tool_response(self, *, function_responses):
            self.responses.extend(function_responses)

    live = FakeLiveSession()
    mapped = _GeminiLiveSession(
        session=live,
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="tool-session",
    )
    events = [event async for event in mapped.receive()]

    assert events[0].type == "tool_call"
    assert events[0].call_id == "call-1"
    assert events[0].tool_args == {"app_name": "Calculator"}

    await mapped.send_tool_result(
        "call-1",
        "open_app",
        {"success": True, "output": "opened", "error": None},
    )
    assert live.responses[0].id == "call-1"
    assert live.responses[0].name == "open_app"
    await provider_session.close()


@pytest.mark.asyncio
async def test_tool_call_suppresses_intermediate_turn_complete() -> None:
    sdk_turns = [
        [
            _fake_message(
                tool_call=SimpleNamespace(
                    function_calls=[
                        SimpleNamespace(id="call-1", name="open_app", args={})
                    ]
                ),
                server_content=SimpleNamespace(
                    output_transcription=None,
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                ),
            )
        ],
        [
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=None,
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            )
        ],
    ]
    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        turn_index = receive_calls - 1
        if turn_index < len(sdk_turns):
            for message in sdk_turns[turn_index]:
                yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="tool-turn",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["tool_call", "turn_complete"]
    assert receive_calls == 3


@pytest.mark.asyncio
async def test_tool_call_suppresses_turn_complete_from_a_later_message() -> None:
    """The live wire splits the tool call and its boundary across messages.

    The sibling test above packs both into ONE message, which is the only
    shape the per-message check ever caught — so the suppression looked
    covered while never firing against a real server. Vertex/Gemini Live sends
    ``toolCall`` first and ``turnComplete`` in a message of its own moments
    later (live 2026-08-20 19:32:41.399: 39 boundaries emitted, 0 withheld).
    The session took each of those for a mute provider and spoke "Erledigt."
    over the user's question while the real answer was thrown away.
    """
    sdk_turns = [
        [
            _fake_message(
                tool_call=SimpleNamespace(
                    function_calls=[
                        SimpleNamespace(id="call-1", name="search_web", args={})
                    ]
                )
            ),
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=None,
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            ),
        ],
        [
            # The generation that carries the spoken answer, once the tool
            # result has travelled. THIS boundary is the turn's real end.
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="Aqua Security …"),
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            )
        ],
    ]
    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        turn_index = receive_calls - 1
        if turn_index < len(sdk_turns):
            for message in sdk_turns[turn_index]:
                yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="split-tool-turn",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "tool_call",
        "output_transcript_delta",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_a_real_mute_after_a_tool_still_reaches_the_session() -> None:
    """Suppression covers the tool-call boundary only, never a genuine mute.

    The counterweight to the two tests above. What the session does with an
    empty boundary — retry the speech from the retained tool result, and
    failing that speak it itself — is the recovery for a model that takes a
    tool result and says nothing. Withholding THAT boundary too would trade a
    wrong answer for a twenty-second silence (the turn-stall watchdog), so the
    generation AFTER the tool result must still close the turn when it produced
    nothing at all.
    """
    sdk_turns = [
        [
            _fake_message(
                tool_call=SimpleNamespace(
                    function_calls=[
                        SimpleNamespace(id="call-1", name="search_web", args={})
                    ]
                )
            ),
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=None,
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            ),
        ],
        [
            # The tool result travelled and the model produced NOTHING.
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=None,
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            )
        ],
    ]
    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        turn_index = receive_calls - 1
        if turn_index < len(sdk_turns):
            for message in sdk_turns[turn_index]:
                yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="mute-after-tool",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["tool_call", "turn_complete"]


def _server_content(
    *,
    output_text: str | None = None,
    interrupted: bool = False,
    turn_complete: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        output_transcription=(
            SimpleNamespace(text=output_text) if output_text is not None else None
        ),
        input_transcription=None,
        interrupted=interrupted,
        turn_complete=turn_complete,
    )


def _tool_call_message(name: str = "search_web") -> SimpleNamespace:
    return _fake_message(
        tool_call=SimpleNamespace(
            function_calls=[SimpleNamespace(id="call-1", name=name, args={})]
        )
    )


def _session_over(sdk_turns: list[list[SimpleNamespace]]) -> _GeminiLiveSession:
    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        turn_index = receive_calls - 1
        if turn_index < len(sdk_turns):
            for message in sdk_turns[turn_index]:
                yield message

    return _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="wire-shape",
    )


@pytest.mark.asyncio
async def test_tool_boundary_survives_the_interrupted_edge_the_server_sends_first() -> None:
    """The REAL wire shape: ``interrupted`` then ``turn_complete`` after a tool call.

    Gemini Live closes a generation that handed out a function call with an
    ``interrupted`` edge immediately followed by ``turn_complete`` (all 7 tool
    calls of the 2026-08-22 18:39 session). The ``interrupted`` boundary reset
    the per-generation function-call counter, so the ``turn_complete`` withhold
    of the two tests above — which keys on that counter — never fired against
    the real server: 12 tool calls that day, 1 withheld, 7 leaked. Every leak
    became "provider completed a direct-tool turn without output; retrying
    speech", and that retry text interrupted the answer already streaming
    ("Gerne. Diese Klage von den Bundesstaaten wirft Meta vor, dass sie ihre  # i18n-allow
    Plattformen" was the whole reply). Neither edge of the pair is a barge-in
    and neither may reach the session.
    """
    session = _session_over(
        [
            [
                _tool_call_message(),
                # Read right after the tool result was sent; the server emitted
                # both edges the moment it handed out the call.
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ],
            [
                # The answer generation — THIS boundary is the turn's real end.
                _fake_message(
                    server_content=_server_content(
                        output_text="Gerne. Diese Klage …", turn_complete=True
                    )
                )
            ],
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "tool_call",
        "output_transcript_delta",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_tool_boundary_pair_packed_into_one_message_is_withheld_whole() -> None:
    session = _session_over(
        [
            [
                _tool_call_message(),
                _fake_message(
                    server_content=_server_content(
                        interrupted=True, turn_complete=True
                    )
                ),
            ],
            [
                _fake_message(
                    server_content=_server_content(
                        output_text="Es gibt zwei Verfahren …", turn_complete=True
                    )
                )
            ],
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "tool_call",
        "output_transcript_delta",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_a_chained_tool_call_keeps_withholding_until_the_spoken_answer() -> None:
    """Tool → pair → second tool → pair → answer: only the answer's end reaches us."""
    session = _session_over(
        [
            [
                _tool_call_message("search_web"),
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ],
            [
                _tool_call_message("search_web"),
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ],
            [
                _fake_message(data=b"\x00\x01"),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ],
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "tool_call",
        "tool_call",
        "audio_delta",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_spoken_output_after_a_lone_interrupted_edge_releases_the_withhold() -> None:
    """A server that skips the ``turn_complete`` half must not mute the answer's end.

    Safety net for the carried evidence: if spoken output follows the tool
    generation's ``interrupted`` edge directly, that output opens a new
    generation, and its ``turn_complete`` is the real boundary the session's
    turn record needs (otherwise only the 20 s stall watchdog would close it).
    """
    session = _session_over(
        [
            [
                _tool_call_message(),
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(data=b"\x00\x01"),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ]
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "tool_call",
        "audio_delta",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_a_real_barge_in_without_a_tool_call_still_interrupts() -> None:
    """The withhold is scoped to tool generations; a user's voice stays a barge-in."""
    session = _session_over(
        [
            [
                _fake_message(data=b"\x00\x01"),
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ]
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "audio_delta",
        "interrupted",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_an_answer_spoken_in_the_tool_generation_closes_the_turn() -> None:
    """Tool call → result → the answer in the SAME generation → its end must reach us.

    Gemini does not always open a new generation for the answer. Live
    2026-08-23 09:25 (and 09:04, 09:23, 09:24 the same morning): the model
    called search_web, took the result, and spoke 18.6 s of answer without a
    generation boundary in between — then sent one ``turn_complete``. The
    per-generation withhold read "this generation called a tool" and swallowed
    it; the desktop pipeline never left JARVIS_SPEAKING, half-duplex kept the
    microphone shut, and the user was deaf until they closed the bar. Spoken
    output AFTER the tool call is what makes this boundary the turn's real end.
    """
    session = _session_over(
        [
            [
                _tool_call_message(),
                # The tool result travelled; the model kept the generation
                # and answered in place.
                _fake_message(data=b"\x00\x01"),
                _fake_message(
                    server_content=_server_content(output_text="The song is called …")
                ),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ]
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "tool_call",
        "audio_delta",
        "output_transcript_delta",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_a_barge_in_over_an_answer_spoken_in_the_tool_generation_interrupts() -> None:
    """Once the tool generation is speaking its answer, the user's voice is a barge-in again."""
    session = _session_over(
        [
            [
                _tool_call_message(),
                _fake_message(data=b"\x00\x01"),
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ]
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "tool_call",
        "audio_delta",
        "interrupted",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_a_preamble_before_the_tool_call_does_not_release_the_withhold() -> None:
    """"Moment …" → tool call → pair: the words BEFORE the call are not its answer."""
    session = _session_over(
        [
            [
                _fake_message(data=b"\x00\x01"),
                _fake_message(server_content=_server_content(output_text="Moment …")),
                _tool_call_message(),
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ],
            [
                _fake_message(
                    server_content=_server_content(
                        output_text="Here is the answer.", turn_complete=True
                    )
                )
            ],
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "audio_delta",
        "output_transcript_delta",
        "tool_call",
        "output_transcript_delta",
        "turn_complete",
    ]


# --- BUG-088: conversation-history seeding into a fresh session -------------


class _SeedableConnectCM:
    """Connect CM whose session records send_client_content calls."""

    def __init__(self) -> None:
        self.exited = False
        self.client_content_calls: list[dict] = []

        async def _send_client_content(*, turns=None, turn_complete=True):
            self.client_content_calls.append(
                {"turns": turns, "turn_complete": turn_complete}
            )

        self.session = SimpleNamespace(send_client_content=_send_client_content)

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        self.exited = True


def _patch_seedable_genai_client(monkeypatch: pytest.MonkeyPatch) -> dict:
    holder: dict = {}

    class _SeedableLiveAPI:
        def __init__(self) -> None:
            self.last_cm: _SeedableConnectCM | None = None
            self.last_config = None

        def connect(self, *, model, config):
            del model
            self.last_config = config
            self.last_cm = _SeedableConnectCM()
            return self.last_cm

    def _make_client(*, api_key=None, **_transport):  # http_options: shared TLS
        client = SimpleNamespace(
            api_key=api_key,
            aio=SimpleNamespace(live=_SeedableLiveAPI()),
            closed=False,
        )
        holder["client"] = client
        return client

    from google import genai

    monkeypatch.setattr(genai, "Client", _make_client)
    return holder


@pytest.mark.asyncio
async def test_open_session_seeds_prior_call_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-088: a mid-call transport rebuild reopens Gemini with a fresh,
    empty conversation. The open must replay the bounded call transcript via
    send_client_content — and per BUG-104 the connection must DECLARE the
    seed (history_config.initial_history_in_client_content) and END it with
    turn_complete=True, otherwise Gemini 3.1 closes the rebuilt connection
    with 1007 right after ready and the call dies mid-sentence."""
    holder = _patch_seedable_genai_client(monkeypatch)
    await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(
            history=(
                {"role": "user", "text": "let's talk programming languages"},
                {"role": "assistant", "text": "Sure — which one interests you?"},
                {"role": "user", "text": "what is the hardest language"},
            )
        )
    )

    live = holder["client"].aio.live
    history_config = live.last_config.history_config
    assert history_config is not None
    assert history_config.initial_history_in_client_content is True
    calls = live.last_cm.client_content_calls
    assert len(calls) == 1
    assert calls[0]["turn_complete"] is True
    turns = calls[0]["turns"]
    assert [turn.role for turn in turns] == ["user", "model", "user"]
    assert [turn.parts[0].text for turn in turns] == [
        "let's talk programming languages",
        "Sure — which one interests you?",
        "what is the hardest language",
    ]


@pytest.mark.asyncio
async def test_open_session_without_history_sends_no_client_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_seedable_genai_client(monkeypatch)
    await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )

    live = holder["client"].aio.live
    assert live.last_cm.client_content_calls == []
    # No declared initial history either — the first open of a call must
    # stay byte-identical to the proven-stable handshake (BUG-104).
    assert live.last_config.history_config is None


@pytest.mark.asyncio
async def test_history_seeding_failure_keeps_the_session_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seeding fails open: an amnesiac session is exactly the pre-BUG-088
    behavior and strictly better than no session at all."""
    async def _broken_send_client_content(*, turns=None, turn_complete=True):
        del turns, turn_complete
        raise RuntimeError("seed rejected")

    class _BrokenSeedCM:
        async def __aenter__(self):
            return SimpleNamespace(
                send_client_content=_broken_send_client_content
            )

        async def __aexit__(self, *_args):
            return None

    def _make_client(*, api_key=None, **_transport):  # http_options: shared TLS
        return SimpleNamespace(
            api_key=api_key,
            aio=SimpleNamespace(
                live=SimpleNamespace(
                    connect=lambda *, model, config: _BrokenSeedCM()
                )
            ),
        )

    from google import genai

    monkeypatch.setattr(genai, "Client", _make_client)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(history=({"role": "user", "text": "hello"},))
    )

    assert session is not None


@pytest.mark.asyncio
async def test_history_seed_construction_failure_keeps_the_session_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK type construction sits inside the fail-open boundary too: a
    Content/Part validation error must degrade to an amnesiac session,
    never fail the provider handshake."""
    holder = _patch_seedable_genai_client(monkeypatch)

    from google.genai import types

    def _explode(*_args, **_kwargs):
        raise ValueError("SDK validation tightened")

    monkeypatch.setattr(types, "Content", _explode)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(history=({"role": "user", "text": "hello"},))
    )

    assert session is not None
    # The connection declared initial-history mode, so the failed seed must
    # still be closed out with an empty turn_complete=True — otherwise the
    # first microphone frame is the next invalid argument (BUG-104).
    assert holder["client"].aio.live.last_cm.client_content_calls == [
        {"turns": None, "turn_complete": True}
    ]


@pytest.mark.asyncio
async def test_sdk_without_history_config_skips_the_seed_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-104: on an SDK that cannot DECLARE initial history, sending the
    seed anyway is a guaranteed server-side 1007 that kills the rebuilt
    connection. The open must skip the seed (amnesiac but alive) — a
    capability probe, never a model-name pin (AP-21)."""
    holder = _patch_seedable_genai_client(monkeypatch)

    from google.genai import types

    monkeypatch.delattr(types, "HistoryConfig")
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(history=({"role": "user", "text": "hello"},))
    )

    assert session is not None
    assert holder["client"].aio.live.last_cm.client_content_calls == []


@pytest.mark.asyncio
async def test_blank_only_history_still_closes_the_declared_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A history whose every entry filters out (blank text) still declared
    initial-history mode at connect — the seed must send the empty
    turn_complete=True terminator so realtime_input becomes legal."""
    holder = _patch_seedable_genai_client(monkeypatch)
    await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(history=({"role": "user", "text": "   "},))
    )

    live = holder["client"].aio.live
    assert live.last_config.history_config is not None
    assert live.last_cm.client_content_calls == [
        {"turns": None, "turn_complete": True}
    ]


# --- function_declarations schema sanitizing --------------------------------


def _sanitize(schema):
    from jarvis.plugins.realtime.gemini_live import _sanitize_schema_for_gemini

    return _sanitize_schema_for_gemini(schema)


def test_sanitizer_strips_additional_properties_recursively() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "nested": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"leaf": {"type": "string"}},
            },
            "listed": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": False},
            },
        },
    }

    result = _sanitize(schema)

    assert "additionalProperties" not in result
    assert "additionalProperties" not in result["properties"]["nested"]
    assert "additionalProperties" not in result["properties"]["listed"]["items"]


def test_sanitizer_preserves_supported_keys() -> None:
    schema = {
        "type": "object",
        "description": "A tool input.",
        "properties": {
            "mode": {"type": "string", "enum": ["fast", "slow"], "default": "fast"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["mode"],
    }

    assert _sanitize(schema) == schema


def test_sanitizer_drops_ref_and_combinators_keeping_siblings() -> None:
    schema = {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"x": {"type": "string"}},
        "oneOf": [{"type": "string"}],
        "properties": {
            "value": {"$ref": "#/$defs/x", "description": "kept sibling"}
        },
    }

    result = _sanitize(schema)

    assert set(result) == {"type", "properties"}
    assert result["properties"]["value"] == {"description": "kept sibling"}


def test_sanitizer_is_idempotent() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"name": {"type": "string", "format": "uri"}},
    }

    once = _sanitize(schema)

    assert _sanitize(once) == once


@pytest.mark.parametrize(
    "module_name, class_name",
    [
        ("jarvis.plugins.tool.describe_app_settings", "DescribeAppSettingsTool"),
        ("jarvis.plugins.tool.dispatch_with_review", "DispatchWithReviewTool"),
        ("jarvis.plugins.tool.manage_mcp_server", "ManageMcpServerTool"),
        ("jarvis.plugins.tool.reveal_key_preview", "RevealKeyPreviewTool"),
        ("jarvis.plugins.tool.switch_provider", "SwitchProviderTool"),
    ],
)
def test_real_router_tool_schemas_survive_sanitizing(
    module_name: str, class_name: str
) -> None:
    """The known additionalProperties carriers must come out Gemini-safe."""
    import importlib

    module = importlib.import_module(module_name)
    tool_cls = getattr(module, class_name)
    schema = getattr(tool_cls, "schema", None)
    if not isinstance(schema, dict):
        instance = tool_cls.__new__(tool_cls)
        schema = getattr(instance, "schema", None)
    assert isinstance(schema, dict), f"{class_name} exposes no dict schema"

    forbidden = {
        "additionalProperties",
        "$schema",
        "$defs",
        "definitions",
        "$ref",
        "oneOf",
        "anyOf",
        "allOf",
        "format",
        "pattern",
        "minLength",
        "maxLength",
    }

    def _assert_clean(node) -> None:
        if isinstance(node, dict):
            assert not (set(node) & forbidden), f"forbidden keys survive: {node}"
            for value in node.values():
                _assert_clean(value)
        elif isinstance(node, list):
            for value in node:
                _assert_clean(value)

    result = _sanitize(schema)

    _assert_clean(result)
    assert result.get("type") == schema.get("type")
    if "properties" in schema:
        assert set(result["properties"]) == set(schema["properties"])


def _session_over_with_text_channel(
    sdk_turns: list[list[SimpleNamespace]],
) -> tuple[_GeminiLiveSession, list[str]]:
    """A wire the adapter can also SEND text on, recording what went out."""
    sent: list[str] = []
    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        turn_index = receive_calls - 1
        if turn_index < len(sdk_turns):
            for message in sdk_turns[turn_index]:
                yield message

    async def fake_send_realtime_input(*, text: str = "", **_: object) -> None:
        sent.append(text)

    session = _GeminiLiveSession(
        session=SimpleNamespace(
            receive=fake_receive,
            send_realtime_input=fake_send_realtime_input,
        ),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="steering-race",
    )
    return session, sent


def _input_transcript_content(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        output_transcription=None,
        input_transcription=SimpleNamespace(text=text),
        interrupted=False,
        turn_complete=False,
    )


@pytest.mark.asyncio
async def test_our_own_steering_text_interrupting_a_reply_supersedes_it() -> None:
    """The live shape of 2026-08-23 10:05 and 10:06, message for message.

    The per-turn directive travels at the final-transcript boundary, which on
    this transport is the same millisecond the server starts answering. When
    it loses that race the text lands as a user turn on an answer already
    streaming: the server abandons that answer (``interrupted``), closes it
    with an EMPTY ``turn_complete``, and generates a replacement seconds
    later. Both halves used to be spoken and recorded as two turns — the
    first cut mid-sentence ("Alles klar. Wenn du nachher noch was brauchst,
    sag" / "Dann wünsche ich dir einen entspannten Start in die neue
    Woche!"). The edge must name itself superseded and the empty boundary
    must not close a turn whose reply has not been spoken yet.
    """
    session, sent = _session_over_with_text_channel(
        [
            [
                # The user's words open the one window steering may travel in.
                _fake_message(server_content=_input_transcript_content("Nee.")),
                # The server had already started answering; its audio and
                # transcript arrive before it reads our text.
                _fake_message(data=b"\x00\x01"),
                _fake_message(
                    server_content=_server_content(output_text="Alles klar.")
                ),
                # Our text lands: the server drops its own answer …
                _fake_message(server_content=_server_content(interrupted=True)),
                # … and closes the abandoned generation with an empty boundary.
                _fake_message(server_content=_server_content(turn_complete=True)),
                # Seconds later, the answer it really means.
                _fake_message(data=b"\x02\x03"),
                _fake_message(
                    server_content=_server_content(output_text="Dann wünsche ich …")
                ),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ]
        ]
    )
    await session.update_session(language="de", turn_directive="Mode: chat.")

    events = [event async for event in session.receive()]

    assert sent, "the queued steering delta must travel on the user's turn"
    assert [event.type for event in events] == [
        "input_transcript",
        "audio_delta",
        "output_transcript_delta",
        "interrupted",
        # No turn_complete here: the abandoned generation ends nothing.
        "audio_delta",
        "output_transcript_delta",
        "turn_complete",
    ]
    interrupted = next(event for event in events if event.type == "interrupted")
    assert interrupted.superseded is True


@pytest.mark.asyncio
async def test_a_barge_in_long_after_our_own_text_still_interrupts() -> None:
    """The counterweight: our text explains only the edges that follow it closely.

    Same wire, but the user talks over a reply seconds after the directive
    went out. That is a real barge-in — it must keep cutting the answer, and
    its boundary must keep closing the turn.
    """
    session, _sent = _session_over_with_text_channel(
        [
            [
                _fake_message(server_content=_input_transcript_content("Ja.")),
                _fake_message(data=b"\x00\x01"),
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ]
        ]
    )
    await session.send_text("a delegate readback")
    # Age our own input past the attribution window without touching the clock
    # every other assertion here depends on.
    session._last_input_at -= 60.0

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "input_transcript",
        "audio_delta",
        "interrupted",
        "turn_complete",
    ]
    interrupted = next(event for event in events if event.type == "interrupted")
    assert interrupted.superseded is False


@pytest.mark.asyncio
async def test_a_second_empty_boundary_after_a_supersede_is_not_withheld() -> None:
    """The withhold is spent by the boundary it was armed for, never latched.

    A server that closes the abandoned generation twice — or that never
    produces the replacement — must not leave the turn open forever: that is
    the shape that froze the desktop pipeline in JARVIS_SPEAKING with the
    microphone shut (2026-08-23 09:25, the tool-boundary sibling).
    """
    session, _sent = _session_over_with_text_channel(
        [
            [
                _fake_message(server_content=_input_transcript_content("Nee.")),
                _fake_message(data=b"\x00\x01"),
                _fake_message(server_content=_server_content(interrupted=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
                _fake_message(server_content=_server_content(turn_complete=True)),
            ]
        ]
    )
    await session.update_session(language="de", turn_directive="Mode: chat.")

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "input_transcript",
        "audio_delta",
        "interrupted",
        "turn_complete",
    ]


# ── transport rebuild must not cost the conversation (BUG-088 parity) ──────


def test_provider_accepts_a_history_snapshot() -> None:
    """The orchestrator's optional capability must actually be here.

    Gemini drops the Live socket on its own schedule and this adapter has no
    in-protocol resume, so ``rebuild_on_transport_death`` reopens a fresh
    session mid-call. Without this method the reopened session could only be
    seeded with the history the CALL STARTED WITH — every turn since was
    lost, which the maintainer experienced on 2026-08-24 10:24 as Jarvis
    forgetting a task he had restated one turn earlier.
    """
    provider = GeminiLiveProvider(api_key="test-key")
    assert provider._history_seed == ()

    provider.set_history_snapshot(
        (
            {"role": "user", "text": "spawn a sub agent for my morning briefing"},
            {"role": "assistant", "text": "Starting one now."},
        )
    )

    assert provider._history_seed == (
        {"role": "user", "text": "spawn a sub agent for my morning briefing"},
        {"role": "assistant", "text": "Starting one now."},
    )


def test_history_snapshot_drops_unusable_entries() -> None:
    """Only user/assistant turns with real text reach the seed."""
    provider = GeminiLiveProvider(api_key="test-key")
    provider.set_history_snapshot(
        (
            {"role": "system", "text": "not a conversation turn"},
            {"role": "user", "text": "   "},
            {"role": "assistant", "text": "kept"},
        )
    )
    assert provider._history_seed == ({"role": "assistant", "text": "kept"},)


def test_every_rebuilding_adapter_can_take_a_snapshot() -> None:
    """Capability parity, not a provider-name check (AP-21).

    An adapter that rebuilds its transport mid-call MUST be able to take the
    updated transcript, or the rebuild silently costs the conversation. Pins
    the pair together so a future rebuilding adapter cannot ship without it.
    """
    for provider in (
        GeminiLiveProvider(api_key="test-key"),
        VertexLiveProvider(api_key="test-key"),
    ):
        if getattr(provider, "rebuild_on_transport_death", False):
            assert callable(getattr(provider, "set_history_snapshot", None)), (
                f"{type(provider).__name__} rebuilds its transport but cannot "
                "restore the conversation into the fresh session"
            )
