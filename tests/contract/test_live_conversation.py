"""Continuous voice survives EventBus, SQLite, API serialization and UI replay."""

import re
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    BrainTurnCompleted,
    BrainTurnStarted,
    Event,
    ReasoningSummaryUpdated,
    VoiceSessionStarted,
    VoiceTranscriptUpdated,
)
from jarvis.live import runtime
from jarvis.live.native import NativeLiveVoiceSession
from jarvis.live.session import LiveVoiceSession
from jarvis.live.state import LiveLedger, TranscriptFragment
from jarvis.live.transcript import LiveTranscript, read_legacy_transcript
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore
from jarvis.speech.pipeline import PipelineState, SpeechPipeline
from jarvis.ui.web.chats_routes import ChatTurn, _live_voice_messages


def feed(projector, role, text, start, end, *, snapshot=False):
    return projector.feed(
        session_id="voice",
        trace_id=uuid4(),
        event_id=str(uuid4()),
        role=role,
        text=text,
        start_ms=start,
        end_ms=end,
        snapshot=snapshot,
    )


def test_fast_followups_and_overlap_keep_distinct_speaker_segments():
    projector = LiveTranscript()
    first = feed(projector, "user", "Hello", 0, 100)
    answer = feed(projector, "assistant", "Hi", 200, 300)
    again = feed(projector, "user", "Hello", 350, 450)
    assert first.segment_id != again.segment_id  # Repetition is not duplicate delivery.
    tail = feed(projector, "assistant", " there", 280, 500)
    assert tail.segment_id == answer.segment_id  # Overlap continues the existing speech.
    assert tail.text == "Hi there"
    assert tail.revision == 2
    assert again.text == "Hello"
    late = feed(projector, "user", "!", 90, 100)
    assert late.segment_id == first.segment_id
    assert late.text == "Hello!"
    newest = feed(projector, "user", " again", 450, 500)
    assert newest.segment_id == again.segment_id
    assert newest.text == "Hello again"


def test_pre_fix_fragment_history_is_recoverable_without_rewriting(tmp_path):
    path = tmp_path / "live.db"
    ledger = LiveLedger(path)
    for i, (role, text) in enumerate([("user", "Hello"), ("assistant", "Hi"), ("user", "Hello")]):
        ledger.append(TranscriptFragment("old", str(i), role, text, i * 400, i * 400 + 100))
    ledger.close()
    before = path.read_bytes()
    assert [(row.role, row.text) for row in read_legacy_transcript(path, "old")] == [
        ("user", "Hello"),
        ("assistant", "Hi"),
        ("user", "Hello"),
    ]
    assert path.read_bytes() == before


def test_running_browser_call_blocks_a_competing_native_start(monkeypatch):
    pipeline = SpeechPipeline.__new__(SpeechPipeline)
    pipeline._state = PipelineState.IDLE
    pipeline._dictation_blocks_activation = lambda: False
    pipeline._capture_permission_allowed = lambda: True
    monkeypatch.setattr(runtime, "_active", {"browser": object()})
    monkeypatch.setattr(runtime, "_opening", set())
    assert not pipeline._activation_allowed()
    # The native parent of this same call is allowed to maintain it.
    pipeline._current_voice_session_id = "browser"
    assert pipeline._activation_allowed()


@pytest.mark.parametrize(
    "event_class,interface",
    [
        (VoiceTranscriptUpdated, "VoiceTranscriptSnapshot"),
        (ReasoningSummaryUpdated, "ReasoningSummarySnapshot"),
    ],
)
def test_python_typescript_payload_parity(event_class, interface):
    source = (
        Path(__file__).resolve().parents[2] / "jarvis/ui/web/frontend/src/lib/liveTranscript.ts"
    ).read_text(encoding="utf-8")
    body = source.split(f"export interface {interface} {{", 1)[1].split("}", 1)[0]
    assert set(re.findall(r"^\s+(\w+):", body, re.MULTILINE)) == (
        {f.name for f in fields(event_class)} - {f.name for f in fields(Event)}
    )
    value = event_class()
    adapter = TypeAdapter(event_class)
    assert adapter.validate_json(adapter.dump_json(value)) == value


@pytest.mark.asyncio
async def test_both_live_adapters_publish_every_speaker_to_the_bus(tmp_path):
    bus = EventBus()
    observed = []

    async def record(event):
        observed.append(event)

    async def send(_frame):
        pass

    bus.subscribe(VoiceTranscriptUpdated, record)
    for cls in (LiveVoiceSession, NativeLiveVoiceSession):
        session = cls(
            session_id="voice",
            bus=bus,
            send_json=send,
            send_binary=send,
            providers=[SimpleNamespace(name="test")],
            config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        )
        ledger = LiveLedger(tmp_path / f"{cls.__name__}.db")
        session._ledger = ledger
        session._tools = SimpleNamespace(user_text="", revision=0, accept_new_input=lambda: None)
        try:
            if cls is LiveVoiceSession:
                for i, (role, text) in enumerate(
                    [("input", "Hello"), ("output", "Hi"), ("input", "Again")]
                ):
                    await session._event(
                        {
                            "type": f"session.{role}_transcript.delta",
                            "delta": text,
                            "event_id": str(i),
                            "start_ms": i * 400,
                            "end_ms": i * 400 + 100,
                        }
                    )
            else:
                for role, text in [
                    ("input_transcript", "Hello"),
                    ("output_transcript_delta", "Hi"),
                    ("input_transcript", "Again"),
                ]:
                    await session._native_event(
                        SimpleNamespace(type=role, text=text, is_final=False)
                    )
            assert [e.role for e in observed[-3:]] == ["user", "assistant", "user"]
        finally:
            ledger.close()


@pytest.mark.asyncio
async def test_recorded_live_conversation_reopens_as_multiple_messages_with_trace(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    bus = EventBus()
    recorder = SessionRecorder(store)
    recorder.attach(bus)
    try:
        await bus.publish(VoiceSessionStarted(session_id="voice", language="en"))
        projector = LiveTranscript()
        await bus.publish(feed(projector, "user", "Read settings", 0, 100))
        await bus.publish(BrainTurnStarted(provider="test", model="thinking"))
        await bus.publish(
            ReasoningSummaryUpdated(
                response_id="reason", text="Checking the selected setting.", done=True
            )
        )
        await bus.publish(BrainTurnCompleted(provider="test", model="thinking"))
        await bus.publish(feed(projector, "assistant", "It is ", 200, 300))
        await bus.publish(feed(projector, "assistant", "enabled.", 300, 400))
        await bus.publish(feed(projector, "user", "Thanks", 500, 600))
        await bus.publish(feed(projector, "assistant", "Welcome", 700, 800))
        rows = store.get_events("voice")
        messages = _live_voice_messages(rows)
        assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
        assert [m.text for m in messages] == [
            "Read settings",
            "It is enabled.",
            "Thanks",
            "Welcome",
        ]
        assert any(e["name"] == "ReasoningSummaryUpdated" for e in messages[1].trace["events"])
        assert [ChatTurn.model_validate_json(m.model_dump_json()) for m in messages] == messages
        # Persisted snapshots retain their field contract, not just a text blob.
        caption = next(e.payload for e in rows if e.kind == "VoiceTranscriptUpdated")
        assert caption["session_id"] == "voice"
        assert caption["segment_id"] and caption["revision"] == 1
    finally:
        store.close()
