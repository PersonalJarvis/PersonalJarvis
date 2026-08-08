"""Polishing an ordinary voice turn — the pass that must never cost a turn.

Three separate promises are pinned here, because breaking any one of them turns
a readability feature into a regression the user feels:

1. **It stays off the critical path.** The brain is handed the raw transcript
   and is already answering; the polish runs beside it. A change that moves the
   pass in front of the brain would still pass every functional test and would
   add the whole latency ceiling to every single turn.
2. **It never publishes a no-op.** Only ``applied`` carries a different string.
   Publishing ``unchanged`` would make every consumer re-render text it already
   has and record a change that never happened.
3. **It attaches to the right turn.** The event arrives on its own clock, so
   the recorder matches it by TEXT. Stamping a late arrival onto whichever turn
   happens to be open is the BUG-090 shape — one person's sentence attributed
   to the next.

No network and no real pipeline: ``_spawn_turn_polish`` reads three attributes
off ``self``, so it is exercised bound to a stand-in.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.core.events import TranscriptPolished
from jarvis.dictation.polish import PolishOutcome
from jarvis.sessions.recorder import _SessionState, _TurnState
from jarvis.sessions.store import SessionStore
from jarvis.speech.pipeline import SpeechPipeline

RAW = "so we should probably move the meeting to the morning and tell the team"
POLISHED = "So we should probably move the meeting to the morning and tell the team."


@dataclass
class _Dictation:
    polish: bool = True
    polish_conversation: bool = True
    polish_style: str = "neutral"
    # Deliberately ON, to prove the turn pass ignores it: translating a
    # conversation RECORD into another language than the one the assistant
    # answered in makes the session history unreadable as a conversation.
    translate: bool = True
    translate_target: str = "en"


@dataclass
class _Config:
    dictation: _Dictation = field(default_factory=_Dictation)


@dataclass
class _FakePipeline:
    """Everything ``_spawn_turn_polish`` touches on ``self`` — and no more."""

    _config: _Config = field(default_factory=_Config)
    published: list[Any] = field(default_factory=list)

    def _dictation_protected_terms(self) -> tuple[str, ...]:
        return ("Jarvis",)

    async def _publish_event(self, event: Any) -> None:
        self.published.append(event)

    def spawn(self, raw: str, *, language: str = "en") -> None:
        SpeechPipeline._spawn_turn_polish(self, raw, language=language)  # type: ignore[arg-type]


async def _settle() -> None:
    """Let the detached task run to completion."""
    for _ in range(6):
        await asyncio.sleep(0)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every call into the polish pass, and answer with ``applied``."""
    seen: list[dict[str, Any]] = []

    async def _fake(text: str, **kwargs: Any) -> PolishOutcome:
        seen.append({"text": text, **kwargs})
        return PolishOutcome(text=POLISHED, status="applied", provider="groq")

    monkeypatch.setattr("jarvis.dictation.polish.polish_transcript", _fake)
    return seen


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #


async def test_nothing_happens_when_the_feature_is_off(
    calls: list[dict[str, Any]],
) -> None:
    pipeline = _FakePipeline(_Config(_Dictation(polish_conversation=False)))

    pipeline.spawn(RAW)
    await _settle()

    assert calls == []
    assert pipeline.published == []


async def test_nothing_happens_when_the_formatter_itself_is_off(
    calls: list[dict[str, Any]],
) -> None:
    """It EXTENDS the formatter; with that off there is nothing to extend."""
    pipeline = _FakePipeline(_Config(_Dictation(polish=False)))

    pipeline.spawn(RAW)
    await _settle()

    assert calls == []


async def test_an_empty_transcript_is_not_sent_anywhere(
    calls: list[dict[str, Any]],
) -> None:
    pipeline = _FakePipeline()

    pipeline.spawn("   ")
    await _settle()

    assert calls == []


# --------------------------------------------------------------------------- #
# What it publishes
# --------------------------------------------------------------------------- #


async def test_a_polished_turn_is_published(calls: list[dict[str, Any]]) -> None:
    pipeline = _FakePipeline()

    pipeline.spawn(RAW)
    await _settle()

    assert len(pipeline.published) == 1
    event = pipeline.published[0]
    assert isinstance(event, TranscriptPolished)
    assert event.text == POLISHED
    # The raw text rides along so a consumer can match the turn it belongs to
    # without depending on event ordering.
    assert event.raw_text == RAW
    assert event.status == "applied"
    assert event.provider == "groq"


async def test_the_call_returns_before_the_pass_does() -> None:
    """The whole point: the turn does not wait.

    The fake blocks until released, so a synchronous implementation would
    deadlock here rather than fail an assertion.
    """
    released = asyncio.Event()

    async def _slow(text: str, **kwargs: Any) -> PolishOutcome:
        await released.wait()
        return PolishOutcome(text=POLISHED, status="applied")

    pipeline = _FakePipeline()
    import jarvis.dictation.polish as polish_mod

    original = polish_mod.polish_transcript
    polish_mod.polish_transcript = _slow  # type: ignore[assignment]
    try:
        pipeline.spawn(RAW)  # must return immediately
        await _settle()
        assert pipeline.published == [], "published before the pass finished"
        released.set()
        await _settle()
        assert len(pipeline.published) == 1
    finally:
        polish_mod.polish_transcript = original  # type: ignore[assignment]


@pytest.mark.parametrize("status", ["unchanged", "rejected_drift", "timeout", "unavailable", "off"])
async def test_only_a_real_rewrite_is_published(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """Every other status hands back exactly what went in."""

    async def _fake(text: str, **kwargs: Any) -> PolishOutcome:
        return PolishOutcome(text=text, status=status)

    monkeypatch.setattr("jarvis.dictation.polish.polish_transcript", _fake)
    pipeline = _FakePipeline()

    pipeline.spawn(RAW)
    await _settle()

    assert pipeline.published == []


async def test_a_failing_pass_never_reaches_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(text: str, **kwargs: Any) -> PolishOutcome:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("jarvis.dictation.polish.polish_transcript", _boom)
    pipeline = _FakePipeline()

    pipeline.spawn(RAW)
    await _settle()

    assert pipeline.published == []


async def test_a_conversation_turn_is_never_translated(
    calls: list[dict[str, Any]],
) -> None:
    """With ``translate`` ON, the turn pass still asks for no translation."""
    pipeline = _FakePipeline()

    pipeline.spawn(RAW)
    await _settle()

    assert calls[0].get("translate_to", "") == ""


async def test_the_users_own_terms_are_protected(
    calls: list[dict[str, Any]],
) -> None:
    pipeline = _FakePipeline()

    pipeline.spawn(RAW)
    await _settle()

    assert "Jarvis" in calls[0]["protected_terms"]


# --------------------------------------------------------------------------- #
# Where it lands
# --------------------------------------------------------------------------- #


def _store(tmp_path: Any) -> SessionStore:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    return store


def test_the_polished_column_survives_an_older_database(tmp_path: Any) -> None:
    """The migration runs on open, and the default is the honest "" ."""
    store = _store(tmp_path)
    try:
        store.upsert_session(
            session_id="s1", started_ms=1_000, language="en", voice_mode="pipeline"
        )
        store.upsert_turn(turn_id="t1", session_id="s1", idx=0, started_ms=1_000)

        store.set_turn_polished(turn_id="t1", text=POLISHED)
        turns = store.get_turns("s1")

        assert turns[0].user_text_polished == POLISHED
        # The record of what was said is untouched.
        assert turns[0].user_text == ""
    finally:
        store.close()


@dataclass
class _RecordingStore:
    written: list[tuple[str, str]] = field(default_factory=list)

    def set_turn_polished(self, *, turn_id: str, text: str) -> None:
        self.written.append((turn_id, text))


def _recorder(store: Any, *, current: Any = None, last: Any = None) -> Any:
    from jarvis.sessions.recorder import SessionRecorder

    recorder = SessionRecorder(store)
    state = _SessionState(session_id="s1", started_ms=0, language="en")
    state.current_turn = current
    state.last_final_turn = last
    recorder._state = state
    return recorder


def _turn(turn_id: str, user_text: str) -> _TurnState:
    return _TurnState(turn_id=turn_id, idx=0, started_ms=0, user_text=user_text)


def _event() -> TranscriptPolished:
    return TranscriptPolished(
        source_layer="speech.stt", text=POLISHED, raw_text=RAW, status="applied"
    )


def test_it_attaches_to_the_open_turn() -> None:
    store = _RecordingStore()
    recorder = _recorder(store, current=_turn("open", RAW))

    recorder._on_transcript_polished(_event())

    assert store.written == [("open", POLISHED)]


def test_it_attaches_to_a_turn_that_was_already_finalized() -> None:
    """The pass lands on its own clock; the turn may already be closed."""
    store = _RecordingStore()
    recorder = _recorder(
        store, current=_turn("next", "something else entirely"), last=_turn("done", RAW)
    )

    recorder._on_transcript_polished(_event())

    assert store.written == [("done", POLISHED)]


def test_it_never_stamps_a_late_arrival_onto_the_wrong_turn() -> None:
    """The BUG-090 shape: one person's sentence attributed to the next.

    A miss is the correct outcome — the raw transcript simply stands, which is
    what every consumer already has.
    """
    store = _RecordingStore()
    recorder = _recorder(
        store,
        current=_turn("next", "a completely different sentence"),
        last=_turn("older", "another unrelated sentence"),
    )

    recorder._on_transcript_polished(_event())

    assert store.written == []


def test_a_store_failure_never_breaks_the_recorder() -> None:
    class _Broken:
        def set_turn_polished(self, **kwargs: Any) -> None:
            raise RuntimeError("disk is gone")

    recorder = _recorder(_Broken(), current=_turn("open", RAW))

    recorder._on_transcript_polished(_event())  # must not raise
