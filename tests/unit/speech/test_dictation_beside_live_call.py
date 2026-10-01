"""The dictation key works DURING a live call instead of hanging it up.

Live 2026-10-01: holding the dictation key in a realtime call ended the call —
the press took the microphone the old way (hang up, wait, record). A live call
captures the microphone in the WebView, so the two never fight over the device:
the dictation records through its own capture while the call's input is HELD
(``VoiceInputHeld``). The model hears nothing until the dictated text has been
delivered, then the conversation continues as if nothing happened.
"""

from __future__ import annotations

import asyncio

import pytest

import jarvis.live.runtime as live_runtime
import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.bus import EventBus
from jarvis.core.events import DictationRefused, DictationStarted, VoiceInputHeld
from jarvis.speech.pipeline import PipelineState, SpeechPipeline, TurnTakingState


class _StubSTT:
    async def transcribe_pcm(self, pcm: bytes):  # pragma: no cover - never called
        raise AssertionError("no transcription in this unit test")


class _SilentMic:
    """A capture device that never yields a frame."""

    opens = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _SilentMic:
        type(self).opens += 1
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def stream(self):  # type: ignore[no-untyped-def]
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable, keeps this an async generator


class _Player:
    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1


class _Collector:
    def __init__(self, bus: EventBus) -> None:
        self.held: list[bool] = []
        self.started: list[DictationStarted] = []
        self.refused: list[DictationRefused] = []
        bus.subscribe(VoiceInputHeld, self._held)
        bus.subscribe(DictationStarted, self._started)
        bus.subscribe(DictationRefused, self._refused)

    async def _held(self, event: VoiceInputHeld) -> None:
        self.held.append(event.held)

    async def _started(self, event: DictationStarted) -> None:
        self.started.append(event)

    async def _refused(self, event: DictationRefused) -> None:
        self.refused.append(event)


def _pipeline(bus: EventBus) -> SpeechPipeline:
    """A pipeline reduced to the dictation lane, sitting in a live call."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._bus = bus
    pipe._utterance_stt = _StubSTT()
    pipe._dictation_task = None
    pipe._dictation_handover_task = None
    pipe._dictation_stop_event = asyncio.Event()
    pipe._dictation_cfg = None
    pipe._dictation_max_s = 5.0
    pipe._dictation_wake_block_until = 0.0
    pipe._dictation_completion_published = True
    pipe._ptt_mode = False
    pipe._ptt_partial_interval_s = 0.0  # no live probe in these tests
    # The desktop pipeline sits in an active session while the call runs.
    pipe._state = PipelineState.ACTIVE
    pipe._turn_state = TurnTakingState.IDLE
    pipe._muted = False
    pipe._input_device = "default"
    pipe._input_priority = ()
    pipe._hangup_event = asyncio.Event()
    pipe._player = _Player()

    async def _no_delivery(**_kwargs) -> str:
        return ""

    pipe._finish_dictation = _no_delivery  # type: ignore[assignment]
    return pipe


async def _drain_bus() -> None:
    for _ in range(6):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _live_call(monkeypatch: pytest.MonkeyPatch):
    """One registered browser call that owns the microphone."""
    monkeypatch.setattr(_SilentMic, "opens", 0)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _SilentMic)
    monkeypatch.setattr(live_runtime, "_active", {"call-1": object()})
    monkeypatch.setattr(live_runtime, "_opening", set())


@pytest.mark.asyncio
async def test_the_key_dictates_without_hanging_up_the_call() -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)

    assert pipe.start_dictation(target="insert") is True
    await _drain_bus()

    assert pipe._dictation_handover_task is None, "no handover beside a live call"
    assert pipe._hangup_event.is_set() is False, "the call is not hung up"
    assert pipe._player.stops == 0
    assert pipe._state is PipelineState.ACTIVE
    assert pipe.dictation_active() is True
    assert _SilentMic.opens == 1, "the dictation records through its own capture"
    assert [e.target for e in seen.started] == ["insert"]
    assert seen.refused == []
    # The call stops hearing the user for as long as the dictation runs.
    assert pipe.is_voice_input_held is True
    assert seen.held == [True]

    task = pipe._dictation_task
    assert task is not None
    pipe.stop_dictation()
    await asyncio.wait_for(task, timeout=2.0)
    await _drain_bus()

    # Delivered — the call gets the user's voice back.
    assert pipe.is_voice_input_held is False
    assert seen.held == [True, False]
    assert pipe._hangup_event.is_set() is False


@pytest.mark.asyncio
async def test_a_cancelled_dictation_still_gives_the_call_its_voice_back() -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)

    assert pipe.start_dictation() is True
    task = pipe._dictation_task
    assert task is not None
    # Cancelled before its first step: no ``finally`` inside it ever runs.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _drain_bus()

    assert pipe.is_voice_input_held is False
    assert seen.held == [True, False]


@pytest.mark.asyncio
async def test_hanging_up_the_call_does_not_throw_the_dictation_away() -> None:
    bus = EventBus()
    _Collector(bus)
    pipe = _pipeline(bus)

    assert pipe.start_dictation() is True
    await _drain_bus()
    pipe._hangup_event.set()
    await _drain_bus()

    assert pipe.dictation_active() is True, "the call's hangup is not the dictation's"

    task = pipe._dictation_task
    assert task is not None
    pipe.stop_dictation()
    await asyncio.wait_for(task, timeout=2.0)
    # The call's hangup request survives the dictation untouched.
    assert pipe._hangup_event.is_set() is True


@pytest.mark.asyncio
async def test_a_pending_call_hangup_is_not_swallowed_by_the_press() -> None:
    bus = EventBus()
    _Collector(bus)
    pipe = _pipeline(bus)
    pipe._hangup_event.set()

    assert pipe.start_dictation() is True
    assert pipe._hangup_event.is_set() is True

    task = pipe._dictation_task
    assert task is not None
    pipe.stop_dictation()
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_push_to_talk_keeps_the_ordinary_handover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PTT records on the pipeline's own stream, so it is not a WebView call."""
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)
    pipe._ptt_mode = True
    hangups: list[bool] = []
    monkeypatch.setattr(pipe, "request_hangup", lambda: hangups.append(True))

    assert pipe.start_dictation() is True
    assert hangups == [True]
    handover = pipe._dictation_handover_task
    assert handover is not None
    handover.cancel()
    await asyncio.gather(handover, return_exceptions=True)
    await _drain_bus()
    assert seen.held == []
    assert pipe.is_voice_input_held is False


@pytest.mark.asyncio
async def test_no_call_means_no_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_runtime, "_active", {})
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)
    pipe._state = PipelineState.IDLE

    assert pipe.start_dictation() is True
    await _drain_bus()
    assert seen.held == []
    assert pipe.is_voice_input_held is False

    task = pipe._dictation_task
    assert task is not None
    pipe.stop_dictation()
    await asyncio.wait_for(task, timeout=2.0)
