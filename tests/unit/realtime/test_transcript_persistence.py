"""End-to-end persistence guards for realtime voice transcript turns."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    SystemStateChanged,
    VoiceSessionEnded,
    VoiceSessionStarted,
)
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore
from tests.fakes.fake_realtime import (
    FakeRealtimeProvider,
    FakeRealtimeToolBridge,
)


async def _delegate_brain(_text: str) -> str:
    return "done"


def _config(provider_name: str, tool_mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(
            reply_language="en",
            providers={
                provider_name: SimpleNamespace(model="live-model", voice="voice")
            },
        ),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(
            mode="realtime",
            realtime_tool_mode=tool_mode,
        ),
    )


def _provider_events(provider_name: str) -> tuple[list[RealtimeEvent], str, list[str]]:
    if provider_name == "gemini-live":
        user_events = [
            RealtimeEvent(type="input_transcript", text="Please open", is_final=True),
            RealtimeEvent(
                type="input_transcript", text="the settings view", is_final=True
            ),
        ]
        # Providers flag transcript fragments final per CHUNK. Every published
        # TranscriptionUpdate must carry the accumulated whole-utterance
        # snapshot, never the raw chunk — downstream surfaces (orb bubble,
        # TranscriptionView, SessionRecorder) mirror the event 1:1 and would
        # otherwise freeze on a single fragment of the sentence.
        expected_snapshots = ["Please open", "Please open the settings view"]
    else:
        user_events = [
            RealtimeEvent(
                type="input_transcript",
                text="Please open the settings view",
                is_final=True,
            )
        ]
        expected_snapshots = ["Please open the settings view"]
    return (
        [
            *user_events,
            RealtimeEvent(
                type="output_transcript_delta",
                text="The settings view is open.",
            ),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x01\x00" * 8,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            ),
            RealtimeEvent(type="turn_complete"),
        ],
        "Please open the settings view",
        expected_snapshots,
    )


async def _start_session(
    *,
    bus: EventBus,
    provider: FakeRealtimeProvider,
    surface: str,
    tool_mode: str,
    session_id: str,
) -> RealtimeVoiceSession:
    supervisor_state = "LISTENING"

    async def send_binary(_data: bytes) -> None:
        nonlocal supervisor_state
        if surface != "desktop" or supervisor_state == "SPEAKING":
            return
        previous, supervisor_state = supervisor_state, "SPEAKING"
        await bus.publish(
            SystemStateChanged(
                source_layer="supervisor",
                previous=previous,
                new_state=supervisor_state,
            )
        )

    async def send_json(message: dict[str, Any]) -> None:
        nonlocal supervisor_state
        if surface != "desktop":
            return
        if (
            message.get("type") == "transcript"
            and message.get("role") == "user"
            and message.get("is_final")
        ):
            previous, supervisor_state = supervisor_state, "THINKING"
        elif message.get("type") == "turn_complete":
            previous, supervisor_state = supervisor_state, "LISTENING"
        else:
            return
        await bus.publish(
            SystemStateChanged(
                source_layer="supervisor",
                previous=previous,
                new_state=supervisor_state,
            )
        )

    if surface == "desktop":
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id=session_id,
                wake_keyword="hotkey",
                language="en",
            )
        )

    kwargs: dict[str, Any]
    if tool_mode == "delegate":
        kwargs = {"brain": _delegate_brain}
    else:
        kwargs = {"tool_bridge": FakeRealtimeToolBridge()}
    session = RealtimeVoiceSession(
        session_id=session_id,
        send_binary=send_binary,
        send_json=send_json,
        providers=[provider],
        config=_config(provider.name, tool_mode),
        bus=bus,
        surface=surface,
        **kwargs,
    )
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    return session


async def _end_session(
    session: RealtimeVoiceSession,
    *,
    bus: EventBus,
    surface: str,
    reason: str,
) -> None:
    await session.end(reason=reason)
    if surface == "desktop":
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id=session.session_id,
                hangup_reason=reason,
                turn_count=1,
            )
        )


@pytest.mark.parametrize("surface", ["desktop", "browser"])
@pytest.mark.parametrize("provider_name", ["gemini-live", "openai-realtime"])
@pytest.mark.parametrize("tool_mode", ["delegate", "direct"])
@pytest.mark.asyncio
async def test_every_realtime_surface_provider_and_tool_mode_persists_complete_turn(
    tmp_path,
    surface: str,
    provider_name: str,
    tool_mode: str,
) -> None:
    events, expected_user, expected_snapshots = _provider_events(provider_name)
    if tool_mode == "delegate":
        output_index = next(
            index
            for index, event in enumerate(events)
            if event.type == "output_transcript_delta"
        )
        events.insert(
            output_index,
            RealtimeEvent(
                type="tool_call",
                call_id="matrix-delegate",
                tool_name="jarvis_action",
                tool_args={"request": expected_user},
            ),
        )
    provider = FakeRealtimeProvider(provider_name, events)
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = await _start_session(
            bus=bus,
            provider=provider,
            surface=surface,
            tool_mode=tool_mode,
            session_id="matrix-session",
        )
        await session.wait_finished()
        reason = "turn_complete" if surface == "desktop" else "ws_closed"
        await _end_session(session, bus=bus, surface=surface, reason=reason)

        turns = store.get_turns("matrix-session")
        assert len(turns) == 1
        assert turns[0].user_text == expected_user
        assert turns[0].jarvis_text == "The settings view is open."
        assert turns[0].tier == "realtime"
        assert turns[0].provider == provider_name
        assert turns[0].model == "live-model"
        assert turns[0].ended_ms is not None
        assert store.list_sessions()[0].hangup_reason == reason
        transcription_events = [
            event.payload["text"]
            for event in store.get_events("matrix-session")
            if event.kind == "TranscriptionUpdate"
        ]
        assert transcription_events == expected_snapshots

        tool_names = [tool["name"] for tool in provider.opened_with.tools]
        if tool_mode == "delegate":
            assert tool_names == ["jarvis_action", "end_call"]
        else:
            assert tool_names == ["open_app", "end_call"]
    finally:
        store.close()


@pytest.mark.parametrize("surface", ["desktop", "browser"])
@pytest.mark.asyncio
async def test_session_end_flushes_pending_turn_without_provider_completion(
    tmp_path, surface: str
) -> None:
    provider = FakeRealtimeProvider(
        "openai-realtime",
        [
            RealtimeEvent(
                type="input_transcript", text="Explain the status", is_final=True
            ),
            RealtimeEvent(
                type="output_transcript_delta", text="The current status is partial."
            ),
            RealtimeEvent(type="error", error="simulated provider stream failure"),
        ],
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = await _start_session(
            bus=bus,
            provider=provider,
            surface=surface,
            tool_mode="direct",
            session_id="unfinished-session",
        )
        await session.wait_finished()
        assert session.failed is True
        reason = "error" if surface == "desktop" else "ws_closed"
        await _end_session(session, bus=bus, surface=surface, reason=reason)

        turns = store.get_turns("unfinished-session")
        assert len(turns) == 1
        assert turns[0].user_text == "Explain the status"
        assert turns[0].jarvis_text == "The current status is partial."
        assert turns[0].tier == "realtime"
        assert turns[0].ended_ms is not None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_streamed_output_deltas_keep_spaces_in_persisted_transcript(
    tmp_path,
) -> None:
    provider = FakeRealtimeProvider(
        "openai-realtime",
        [
            RealtimeEvent(
                type="input_transcript", text="Can you help?", is_final=True
            ),
            RealtimeEvent(type="output_transcript_delta", text="All"),
            RealtimeEvent(type="output_transcript_delta", text=" right"),
            RealtimeEvent(type="output_transcript_delta", text=", "),
            RealtimeEvent(type="output_transcript_delta", text="I"),
            RealtimeEvent(type="output_transcript_delta", text=" can help"),
            RealtimeEvent(type="output_transcript_delta", text="."),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = await _start_session(
            bus=bus,
            provider=provider,
            surface="browser",
            tool_mode="direct",
            session_id="streamed-spacing-session",
        )
        await session.wait_finished()
        await _end_session(session, bus=bus, surface="browser", reason="ws_closed")

        turns = store.get_turns("streamed-spacing-session")
        assert len(turns) == 1
        assert turns[0].jarvis_text == "All right, I can help."
    finally:
        store.close()


@pytest.mark.asyncio
async def test_assistant_output_without_input_transcript_still_persists_turn(
    tmp_path,
) -> None:
    provider = FakeRealtimeProvider(
        "openai-realtime",
        [
            RealtimeEvent(
                type="output_transcript_delta",
                text="I heard audio, but its transcript was unavailable.",
            ),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = await _start_session(
            bus=bus,
            provider=provider,
            surface="browser",
            tool_mode="direct",
            session_id="no-input-transcript",
        )
        await session.wait_finished()
        await _end_session(session, bus=bus, surface="browser", reason="ws_closed")

        turns = store.get_turns("no-input-transcript")
        assert len(turns) == 1
        assert turns[0].user_text == ""
        assert turns[0].jarvis_text == (
            "I heard audio, but its transcript was unavailable."
        )
        assert turns[0].tier == "realtime"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_transcription_failure_keeps_truthful_empty_user_text_and_error_event(
    tmp_path,
) -> None:
    provider = FakeRealtimeProvider(
        "openai-realtime",
        [
            RealtimeEvent(
                type="input_transcript",
                text="",
                is_final=True,
                error="input transcription failed",
            ),
            RealtimeEvent(
                type="output_transcript_delta", text="Please repeat that request."
            ),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = await _start_session(
            bus=bus,
            provider=provider,
            surface="browser",
            tool_mode="direct",
            session_id="failed-transcription",
        )
        await session.wait_finished()
        await _end_session(session, bus=bus, surface="browser", reason="ws_closed")

        turns = store.get_turns("failed-transcription")
        assert len(turns) == 1
        assert turns[0].user_text == ""
        assert turns[0].jarvis_text == "Please repeat that request."
        errors = [
            event.payload
            for event in store.get_events("failed-transcription")
            if event.kind == "ErrorOccurred"
        ]
        assert errors[0]["error_type"] == "RealtimeTranscriptionError"
        assert errors[0]["recoverable"] is True
    finally:
        store.close()


@pytest.mark.asyncio
async def test_repeated_browser_audio_start_does_not_split_pending_turn(tmp_path) -> None:
    provider = FakeRealtimeProvider(
        "gemini-live",
        [
            RealtimeEvent(
                type="input_transcript", text="Keep this turn", is_final=True
            ),
            RealtimeEvent(
                type="output_transcript_delta", text="This answer is still pending."
            ),
        ],
        hold_after_events=True,
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = await _start_session(
            bus=bus,
            provider=provider,
            surface="browser",
            tool_mode="direct",
            session_id="browser-restart",
        )
        assert provider.session is not None
        await asyncio.wait_for(provider.session.events_drained.wait(), timeout=1.0)
        await session.handle_control({"type": "audio_start", "sample_rate": 48_000})
        await _end_session(session, bus=bus, surface="browser", reason="client_stop")

        turns = store.get_turns("browser-restart")
        assert len(turns) == 1
        assert turns[0].user_text == "Keep this turn"
        assert turns[0].jarvis_text == "This answer is still pending."
        assert store.list_sessions()[0].hangup_reason == "client_stop"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("provider_name", "boundary_event", "initial_events"),
    [
        (
            "openai-realtime",
            RealtimeEvent(type="speech_started"),
            [RealtimeEvent(type="speech_started")],
        ),
        ("gemini-live", RealtimeEvent(type="interrupted"), []),
    ],
)
@pytest.mark.asyncio
async def test_barge_in_finalizes_previous_turn_before_next_user_transcript(
    tmp_path,
    provider_name: str,
    boundary_event: RealtimeEvent,
    initial_events: list[RealtimeEvent],
) -> None:
    provider = FakeRealtimeProvider(
        provider_name,
        [
            *initial_events,
            RealtimeEvent(type="input_transcript", text="First question", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text="First partial answer."),
            boundary_event,
            RealtimeEvent(type="input_transcript", text="Second question", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text="Second answer."),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = await _start_session(
            bus=bus,
            provider=provider,
            surface="browser",
            tool_mode="direct",
            session_id="barge-session",
        )
        await session.wait_finished()
        await _end_session(session, bus=bus, surface="browser", reason="ws_closed")

        turns = store.get_turns("barge-session")
        assert [(turn.user_text, turn.jarvis_text) for turn in turns] == [
            ("First question", "First partial answer."),
            ("Second question", "Second answer."),
        ]
        assert all(turn.tier == "realtime" for turn in turns)
        assert all(turn.ended_ms is not None for turn in turns)
        assert provider.session is not None
        if boundary_event.type == "interrupted":
            # The provider already cancelled this response. Sending a second
            # cancel produces response_cancel_not_active on live Gemini.
            assert provider.session.interrupts == 0
        else:
            assert provider.session.interrupts >= 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_terminal_provider_error_still_plays_gate_held_audio_tail(
    tmp_path,
) -> None:
    """A terminal provider failure mid-reply must not swallow the tail audio
    that the scrub gate already holds under a cleared transcript. Live
    incident 2026-07-15 17:40 (gemini-live): the reply text was fully stored
    while the spoken audio stopped mid-answer when the transport died."""
    tail_pcm = b"\xaa\x00" * 8
    provider = FakeRealtimeProvider(
        "gemini-live",
        [
            RealtimeEvent(
                type="input_transcript", text="What day is tomorrow?", is_final=True
            ),
            RealtimeEvent(
                type="output_transcript_delta", text="Tomorrow is Thursday."
            ),
            # First delta consumes the transcript clearance credit...
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x01\x00" * 8, sample_rate=24_000, timestamp_ns=0
                ),
            ),
            # ...so this tail chunk stays buffered inside the scrub gate.
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(pcm=tail_pcm, sample_rate=24_000, timestamp_ns=0),
            ),
            RealtimeEvent(type="error", error="simulated transport death"),
        ],
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    played: list[bytes] = []

    async def send_binary(data: bytes) -> None:
        played.append(bytes(data))

    async def send_json(_message: dict[str, Any]) -> None:
        return None

    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = RealtimeVoiceSession(
            session_id="tail-audio-session",
            send_binary=send_binary,
            send_json=send_json,
            providers=[provider],
            config=_config("gemini-live", "direct"),
            bus=bus,
            surface="browser",
            tool_bridge=FakeRealtimeToolBridge(),
        )
        await session.handle_control({"type": "audio_start", "sample_rate": 48_000})
        await session.wait_finished()
        assert session.failed is True
        await session.end(reason="error")

        assert any(tail_pcm in chunk for chunk in played), (
            "the transcript-cleared tail audio was dropped on the error path"
        )
        turns = store.get_turns("tail-audio-session")
        assert len(turns) == 1
        assert turns[0].jarvis_text == "Tomorrow is Thursday."
    finally:
        store.close()


@pytest.mark.asyncio
async def test_silent_stream_end_mid_turn_fails_the_session_and_plays_the_tail(
    tmp_path,
) -> None:
    """The Gemini SDK's receive() can end without an exception and without a
    turn boundary. Mid-turn that is a silent transport death: the session must
    reach the same fail-closed path as an explicit provider error — failed
    flag set, provider_error surfaced, transcript-cleared tail audio played —
    instead of hanging as active with the tail dropped."""
    tail_pcm = b"\xbb\x00" * 8
    provider = FakeRealtimeProvider(
        "gemini-live",
        [
            RealtimeEvent(
                type="input_transcript", text="What day is tomorrow?", is_final=True
            ),
            RealtimeEvent(
                type="output_transcript_delta", text="Tomorrow is Thursday."
            ),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x01\x00" * 8, sample_rate=24_000, timestamp_ns=0
                ),
            ),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(pcm=tail_pcm, sample_rate=24_000, timestamp_ns=0),
            ),
            # No error event, no turn_complete: the iterator just ends.
        ],
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    played: list[bytes] = []
    statuses: list[dict[str, Any]] = []

    async def send_binary(data: bytes) -> None:
        played.append(bytes(data))

    async def send_json(message: dict[str, Any]) -> None:
        statuses.append(dict(message))

    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = RealtimeVoiceSession(
            session_id="silent-stream-end",
            send_binary=send_binary,
            send_json=send_json,
            providers=[provider],
            config=_config("gemini-live", "direct"),
            bus=bus,
            surface="browser",
            tool_bridge=FakeRealtimeToolBridge(),
        )
        await session.handle_control({"type": "audio_start", "sample_rate": 48_000})
        await session.wait_finished()

        assert session.failed is True
        assert any(m.get("type") == "provider_error" for m in statuses)
        assert any(tail_pcm in chunk for chunk in played), (
            "the transcript-cleared tail audio was dropped on the silent "
            "stream-end path"
        )
        await session.end(reason="error")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_handshake_fallback_turn_records_effective_provider(tmp_path) -> None:
    failed = FakeRealtimeProvider(
        "gemini-live",
        [],
        open_error=RuntimeError("simulated handshake failure"),
    )
    working = FakeRealtimeProvider(
        "openai-realtime",
        [
            RealtimeEvent(type="input_transcript", text="Hello", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text="Hi."),
            RealtimeEvent(type="turn_complete"),
        ],
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        session = RealtimeVoiceSession(
            session_id="provider-fallback",
            send_binary=lambda _data: asyncio.sleep(0),
            send_json=lambda _message: asyncio.sleep(0),
            providers=[failed, working],
            config=_config("openai-realtime", "direct"),
            bus=bus,
            surface="browser",
            tool_bridge=FakeRealtimeToolBridge(),
        )
        await session.handle_control({"type": "audio_start", "sample_rate": 48_000})
        await session.wait_finished()
        await session.end(reason="ws_closed")

        turns = store.get_turns("provider-fallback")
        assert len(turns) == 1
        assert turns[0].provider == "openai-realtime"
        assert turns[0].model == "live-model"
    finally:
        store.close()
