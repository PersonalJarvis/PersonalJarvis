"""The dictation turn announces itself: started → transcribing → completed.

Before this, the only signals a dictation produced were the live transcript and
the completion — both at or after the END. A surface therefore could not show
"listening" until the first partial arrived (a partial interval plus an STT
round-trip later, and never at all for a short press), and a refused start was
a ``log.info`` in a file the desktop app cannot display (CLAUDE.md §9), so the
shortcut simply did nothing with no way to learn why.

Pinned here:

* ``DictationStarted`` is published before any audio is captured.
* ``DictationTranscribing`` is published when the microphone lease closes.
* ``DictationCompleted`` is published on EVERY end path, including a task
  killed mid-flight — otherwise a surface stays open on a dictation that is
  not running.
* Every refusal publishes ``DictationRefused`` with a reason from the one
  shared vocabulary.
"""

from __future__ import annotations

import asyncio

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    DICTATION_REFUSAL_REASONS,
    DictationCompleted,
    DictationRefused,
    DictationStarted,
    DictationTranscribing,
)
from jarvis.speech.pipeline import PipelineState, SpeechPipeline


class _StubSTT:
    async def transcribe_pcm(self, pcm: bytes):  # pragma: no cover - never called
        raise AssertionError("no transcription in this unit test")


class _FakeMic:
    """A microphone that opens, never delivers a frame, and closes cleanly."""

    opened = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeMic:
        type(self).opened += 1
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def stream(self):  # type: ignore[no-untyped-def]
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable, keeps this an async generator


class _Collector:
    def __init__(self, bus: EventBus) -> None:
        self.started: list[DictationStarted] = []
        self.transcribing: list[DictationTranscribing] = []
        self.completed: list[DictationCompleted] = []
        self.refused: list[DictationRefused] = []
        bus.subscribe(DictationStarted, self._started)
        bus.subscribe(DictationTranscribing, self._transcribing)
        bus.subscribe(DictationCompleted, self._completed)
        bus.subscribe(DictationRefused, self._refused)

    async def _started(self, event: DictationStarted) -> None:
        self.started.append(event)

    async def _transcribing(self, event: DictationTranscribing) -> None:
        self.transcribing.append(event)

    async def _completed(self, event: DictationCompleted) -> None:
        self.completed.append(event)

    async def _refused(self, event: DictationRefused) -> None:
        self.refused.append(event)


def _dictation_pipeline(bus: EventBus) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._bus = bus
    pipe._utterance_stt = _StubSTT()
    pipe._dictation_task = None
    pipe._dictation_stop_event = asyncio.Event()
    pipe._dictation_cfg = None
    pipe._dictation_max_s = 5.0
    pipe._dictation_wake_block_until = 0.0
    pipe._dictation_completion_published = True
    pipe._ptt_mode = False
    pipe._ptt_partial_interval_s = 0.0  # no live probe in these tests
    pipe._state = PipelineState.IDLE
    pipe._muted = False
    pipe._input_device = "default"
    pipe._input_priority = ()
    pipe._hangup_event = asyncio.Event()
    return pipe


async def _drain_bus() -> None:
    """Let the scheduled publish tasks run (``_publish_event_soon``)."""
    for _ in range(4):
        await asyncio.sleep(0)


async def _cancel_dictation(pipe: SpeechPipeline) -> None:
    task = pipe._dictation_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


# --------------------------------------------------------------------------
# started
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_announces_the_turn_before_any_audio(monkeypatch) -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _dictation_pipeline(bus)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _FakeMic)
    opened_before = _FakeMic.opened

    assert pipe.start_dictation(target="insert") is True
    assert _FakeMic.opened == opened_before, (
        "the turn is announced at the commit point — no audio yet"
    )

    await _drain_bus()

    assert len(seen.started) == 1
    assert seen.started[0].target == "insert"
    assert seen.started[0].source_layer == "speech.dictation"

    await _cancel_dictation(pipe)


@pytest.mark.asyncio
async def test_start_arms_the_wake_watchdog_deadline(monkeypatch) -> None:
    bus = EventBus()
    _Collector(bus)
    pipe = _dictation_pipeline(bus)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _FakeMic)

    assert pipe.start_dictation() is True
    # Bounded: recording cap + a finalization allowance, never open-ended.
    assert pipe._dictation_blocks_activation() is True
    assert pipe._activation_allowed() is False

    await _cancel_dictation(pipe)
    assert pipe._activation_allowed() is True


# --------------------------------------------------------------------------
# transcribing + completed
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_releasing_the_key_announces_the_transcribing_phase(monkeypatch) -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _dictation_pipeline(bus)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _FakeMic)

    async def _fake_finish(**kwargs: object) -> str:
        pipe._dictation_completion_published = True
        await bus.publish(DictationCompleted(source_layer="speech.dictation"))
        return ""

    pipe._finish_dictation = _fake_finish  # type: ignore[method-assign]

    assert pipe.start_dictation() is True
    await _drain_bus()
    pipe.stop_dictation()
    await asyncio.wait_for(pipe._dictation_task, timeout=5.0)
    await _drain_bus()

    assert len(seen.transcribing) == 1, (
        "the transcribing phase is announced exactly once, when the microphone lease closes"
    )
    assert len(seen.completed) == 1
    # Ordering is the whole point: listening → transcribing → done.
    assert seen.started[0].timestamp_ns <= seen.transcribing[0].timestamp_ns


@pytest.mark.asyncio
async def test_a_dictation_killed_mid_flight_still_closes_its_turn(
    monkeypatch,
) -> None:
    """A cancelled task must not leave a surface stuck showing a dictation."""
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _dictation_pipeline(bus)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _FakeMic)

    assert pipe.start_dictation() is True
    await _drain_bus()
    assert len(seen.started) == 1
    assert seen.completed == []

    await _cancel_dictation(pipe)
    await _drain_bus()

    assert len(seen.completed) == 1
    assert seen.completed[0].outcome == "cancelled"
    assert seen.completed[0].detail
    # ...and wake is listening again.
    assert pipe._activation_allowed() is True


@pytest.mark.asyncio
async def test_a_crashing_dictation_publishes_exactly_one_completion(
    monkeypatch,
) -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _dictation_pipeline(bus)

    class _ExplodingMic(_FakeMic):
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("microphone is on fire")

    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _ExplodingMic)

    async def _fake_finish(**kwargs: object) -> str:
        pipe._dictation_completion_published = True
        await bus.publish(DictationCompleted(source_layer="speech.dictation", outcome="failed"))
        return ""

    pipe._finish_dictation = _fake_finish  # type: ignore[method-assign]

    assert pipe.start_dictation() is True
    await asyncio.wait_for(pipe._dictation_task, timeout=5.0)
    await _drain_bus()

    assert len(seen.completed) == 1, "the teardown must never double-publish"
    assert seen.completed[0].outcome == "failed"
    assert pipe._activation_allowed() is True


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_closed_capture_gate_is_refused_out_loud() -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _dictation_pipeline(bus)
    pipe._activation_gate = lambda: False

    assert pipe.start_dictation() is False
    await _drain_bus()

    assert [e.reason for e in seen.refused] == ["microphone_unavailable"]
    assert seen.refused[0].detail
    assert seen.started == []


@pytest.mark.asyncio
async def test_a_missing_stt_provider_is_refused_out_loud() -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _dictation_pipeline(bus)
    pipe._utterance_stt = None

    assert pipe.start_dictation() is False
    await _drain_bus()

    assert [e.reason for e in seen.refused] == ["no_stt"]


@pytest.mark.asyncio
async def test_a_second_start_while_recording_is_refused_out_loud(
    monkeypatch,
) -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _dictation_pipeline(bus)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _FakeMic)

    assert pipe.start_dictation() is True
    assert pipe.start_dictation() is False
    await _drain_bus()

    assert [e.reason for e in seen.refused] == ["already_running"]

    await _cancel_dictation(pipe)


@pytest.mark.asyncio
async def test_a_voice_session_that_will_not_let_go_is_refused_out_loud(
    monkeypatch,
) -> None:
    """The collision case is a TAKEOVER now, not a refusal.

    An explicit key press beats a conversation somebody left open, so the
    session is hung up and the dictation starts. Only a session that does not
    give the microphone back — nothing here ever returns this pipeline to IDLE —
    is still a dead end, and it says so out loud instead of doing nothing at all.
    The takeover itself is pinned in
    ``test_dictation_takes_over_voice_session.py``.
    """
    monkeypatch.setattr(pipeline_mod, "_DICTATION_HANDOVER_TIMEOUT_S", 0.05)
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _dictation_pipeline(bus)
    pipe._state = PipelineState.ACTIVE

    assert pipe.start_dictation() is True
    handover = pipe._dictation_handover_task
    assert handover is not None
    await asyncio.wait_for(handover, timeout=2.0)
    await _drain_bus()

    assert [e.reason for e in seen.refused] == ["handover_failed"]
    assert "microphone" in seen.refused[0].detail.lower()
    assert seen.started == []

    # Push-to-talk is the same collision through a different door.
    pipe._state = PipelineState.IDLE
    pipe._ptt_mode = True
    seen.refused.clear()
    assert pipe.start_dictation() is True
    handover = pipe._dictation_handover_task
    assert handover is not None
    await asyncio.wait_for(handover, timeout=2.0)
    await _drain_bus()
    assert [e.reason for e in seen.refused] == ["handover_failed"]


@pytest.mark.asyncio
async def test_every_refusal_reason_comes_from_the_shared_vocabulary(
    monkeypatch,
) -> None:
    """AP-4: the reason token crosses pipeline → bus → REST → UI."""
    monkeypatch.setattr(pipeline_mod, "_DICTATION_HANDOVER_TIMEOUT_S", 0.0)
    bus = EventBus()
    seen = _Collector(bus)

    closed_gate = _dictation_pipeline(bus)
    closed_gate._activation_gate = lambda: False
    closed_gate.start_dictation()

    no_stt = _dictation_pipeline(bus)
    no_stt._utterance_stt = None
    no_stt.start_dictation()

    busy = _dictation_pipeline(bus)
    busy._state = PipelineState.ACTIVE
    busy.start_dictation()
    handover = busy._dictation_handover_task
    assert handover is not None
    await asyncio.wait_for(handover, timeout=2.0)

    await _drain_bus()

    assert seen.refused, "the refusals must actually reach the bus"
    for event in seen.refused:
        assert event.reason in DICTATION_REFUSAL_REASONS
        assert event.detail.strip(), "a refusal without a sentence is not observable"
