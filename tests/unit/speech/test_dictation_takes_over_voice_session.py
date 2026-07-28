"""The dictation shortcut WINS over a conversation somebody left open.

The live defect this pins: ``start_dictation`` refused whenever a voice session
was running, and the refusal only ever reached a log file the desktop app cannot
display. On the maintainer's configuration such a session never ends by itself
(idle auto-hangup is off by mandate, the hangup key is unbound, conversation
mode is on), so ONE wake word at any point in the day made every later dictation
key press do nothing at all — no bar, no transcript, no error — until the app
was restarted.

The press is now the deliberate action and the background session is not: the
conversation is hung up through the ordinary chokepoint (the same one the bar's
close-X uses) and the dictation starts once the microphone has actually come
back. Three properties carry the whole change, and each has a bug behind it:

* **Order** — the session's lease is released BEFORE the dictation claims one.
  Two native input streams on one device is the AP-24 / BUG-014 family.
* **Bound** — a teardown that raises or wedges refuses honestly instead of
  hanging, so a stuck session can never make the key permanently dead again.
* **Visibility** — every one of those outcomes publishes ``DictationRefused``
  with a sentence a non-technical person can act on, and the wake gate is open
  again afterwards on every path (a stuck gate is BUG-037: permanently deaf).
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
)
from jarvis.speech.pipeline import PipelineState, SpeechPipeline, TurnTakingState


class _StubSTT:
    async def transcribe_pcm(self, pcm: bytes):  # pragma: no cover - never called
        raise AssertionError("no transcription in this unit test")


class _MicLedger:
    """Counts how many microphone leases are open AT THE SAME TIME."""

    def __init__(self) -> None:
        self.live = 0
        self.peak = 0
        self.opens = 0
        self.order: list[str] = []


class _CountingMic:
    """A capture device that never yields a frame and books itself in a ledger."""

    ledger = _MicLedger()

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _CountingMic:
        ledger = type(self).ledger
        ledger.opens += 1
        ledger.live += 1
        ledger.peak = max(ledger.peak, ledger.live)
        ledger.order.append("open")
        return self

    async def __aexit__(self, *exc: object) -> bool:
        ledger = type(self).ledger
        ledger.live -= 1
        ledger.order.append("close")
        return False

    async def stream(self):  # type: ignore[no-untyped-def]
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable, keeps this an async generator


class _Player:
    """Just enough of the audio player to record the hard kill-switch."""

    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1


class _Collector:
    def __init__(self, bus: EventBus) -> None:
        self.started: list[DictationStarted] = []
        self.completed: list[DictationCompleted] = []
        self.refused: list[DictationRefused] = []
        bus.subscribe(DictationStarted, self._started)
        bus.subscribe(DictationCompleted, self._completed)
        bus.subscribe(DictationRefused, self._refused)

    async def _started(self, event: DictationStarted) -> None:
        self.started.append(event)

    async def _completed(self, event: DictationCompleted) -> None:
        self.completed.append(event)

    async def _refused(self, event: DictationRefused) -> None:
        self.refused.append(event)


def _pipeline(bus: EventBus) -> SpeechPipeline:
    """A pipeline reduced to the dictation lane plus a hangup chokepoint."""
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
    pipe._state = PipelineState.IDLE
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
    """Let the scheduled publish tasks run (``_publish_event_soon``)."""
    for _ in range(6):
        await asyncio.sleep(0)


def _handover_of(pipe: SpeechPipeline) -> asyncio.Task[None]:
    """The in-flight handover task, captured before it clears its own handle."""
    task = pipe._dictation_handover_task
    assert task is not None, "an accepted press must have started a handover"
    return task


async def _end_dictation(pipe: SpeechPipeline) -> None:
    task = pipe._dictation_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


def _run_voice_session(pipe: SpeechPipeline) -> asyncio.Task[None]:
    """A stand-in for the state loop that owns the microphone until a hangup.

    It mirrors the ONE ordering guarantee that matters: the capture context is
    exited before ``_state`` returns to IDLE, so anything that waits for IDLE is
    guaranteed the device is free.
    """

    async def _session() -> None:
        pipe._state = PipelineState.ACTIVE
        async with pipeline_mod.MicrophoneCapture(device="default"):
            await pipe._hangup_event.wait()
        pipe._ptt_mode = False
        pipe._state = PipelineState.IDLE

    return asyncio.create_task(_session(), name="fake-voice-session")


@pytest.fixture(autouse=True)
def _fresh_mic_ledger(monkeypatch: pytest.MonkeyPatch) -> _MicLedger:
    ledger = _MicLedger()
    monkeypatch.setattr(_CountingMic, "ledger", ledger)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _CountingMic)
    return ledger


# --------------------------------------------------------------------------
# The takeover itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_key_takes_the_microphone_from_a_live_voice_session() -> None:
    """The headline case: one open conversation used to kill the shortcut."""
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)
    session = _run_voice_session(pipe)
    await asyncio.sleep(0)
    assert pipe._state is PipelineState.ACTIVE

    assert pipe.start_dictation(target="insert") is True, (
        "an explicit key press is accepted, not refused"
    )
    handover = _handover_of(pipe)

    await asyncio.wait_for(session, timeout=2.0)
    await asyncio.wait_for(handover, timeout=2.0)
    await _drain_bus()

    # The conversation is gone and the dictation is recording.
    assert pipe._state is PipelineState.IDLE
    assert pipe._dictation_task is not None
    assert pipe.dictation_active() is True
    assert [e.target for e in seen.started] == ["insert"]
    assert seen.refused == []
    # The stale hangup is cleared, or the fresh dictation would end on its
    # first tick (the lane shares ``_hangup_event``).
    assert pipe._hangup_event.is_set() is False

    await _end_dictation(pipe)


@pytest.mark.asyncio
async def test_a_mid_answer_takeover_stops_the_voice_the_clean_way() -> None:
    """Deliberate: an explicit stop gesture beats a half-spoken answer.

    The maintainer's kill-switch doctrine ("auflegen" stops the player at once)
    applies to the dictation key too — otherwise the takeover would work for an
    idle-but-open session and silently fail for the exact case a user reaches
    for the key in. What must NOT happen is a torn-down-by-hand pipeline: the
    cut goes through the ordinary hangup chokepoint, so the session unwinds the
    way it does for the bar's close-X.
    """
    bus = EventBus()
    _Collector(bus)
    pipe = _pipeline(bus)
    pipe._turn_state = TurnTakingState.JARVIS_SPEAKING
    session = _run_voice_session(pipe)
    await asyncio.sleep(0)

    assert pipe.start_dictation() is True
    handover = _handover_of(pipe)
    await asyncio.wait_for(session, timeout=2.0)
    await asyncio.wait_for(handover, timeout=2.0)

    assert pipe._player.stops == 1, "the spoken answer is cut immediately"
    assert pipe._dictation_task is not None

    await _end_dictation(pipe)


@pytest.mark.asyncio
async def test_push_to_talk_is_the_same_collision_through_another_door() -> None:
    """PTT arms before the state machine leaves IDLE, so it needs its own half."""
    bus = EventBus()
    _Collector(bus)
    pipe = _pipeline(bus)
    pipe._ptt_mode = True
    session = _run_voice_session(pipe)
    await asyncio.sleep(0)

    assert pipe.start_dictation() is True
    handover = _handover_of(pipe)
    await asyncio.wait_for(session, timeout=2.0)
    await asyncio.wait_for(handover, timeout=2.0)

    assert pipe._ptt_mode is False
    assert pipe._dictation_task is not None

    await _end_dictation(pipe)


# --------------------------------------------------------------------------
# One owner of the microphone, always (AP-24)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_microphone_is_never_held_by_two_owners_across_the_handover(
    _fresh_mic_ledger: _MicLedger,
) -> None:
    """The ordering guarantee. Two native streams on one device wedge it."""
    bus = EventBus()
    _Collector(bus)
    pipe = _pipeline(bus)
    session = _run_voice_session(pipe)
    await asyncio.sleep(0)
    assert _fresh_mic_ledger.live == 1

    assert pipe.start_dictation() is True
    handover = _handover_of(pipe)
    await asyncio.wait_for(session, timeout=2.0)
    await asyncio.wait_for(handover, timeout=2.0)
    # Let the dictation task actually open its capture.
    for _ in range(10):
        await asyncio.sleep(0)

    assert _fresh_mic_ledger.opens == 2, "session, then dictation — not one shared"
    assert _fresh_mic_ledger.peak == 1, (
        "the session's lease must close before the dictation claims one"
    )
    assert _fresh_mic_ledger.order[:3] == ["open", "close", "open"]

    await _end_dictation(pipe)
    assert _fresh_mic_ledger.live == 0


# --------------------------------------------------------------------------
# A handover that cannot finish refuses OUT LOUD
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_teardown_that_never_finishes_refuses_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wedged-session case: bounded, honest, and wake is listening again."""
    monkeypatch.setattr(pipeline_mod, "_DICTATION_HANDOVER_TIMEOUT_S", 0.05)
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)
    pipe._state = PipelineState.ACTIVE  # nothing will ever set this back

    assert pipe.start_dictation() is True
    handover = pipe._dictation_handover_task
    assert handover is not None
    await asyncio.wait_for(handover, timeout=2.0)
    await _drain_bus()

    assert [e.reason for e in seen.refused] == ["handover_failed"]
    assert seen.refused[0].detail.endswith(".")
    assert seen.started == [], "nothing may claim to be recording"
    assert pipe._dictation_task is None
    # BUG-037: the gate a failed handover closed must be open again.
    assert pipe._dictation_blocks_activation() is False
    assert pipe._activation_allowed() is True


@pytest.mark.asyncio
async def test_a_hangup_that_raises_refuses_with_a_reason() -> None:
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)
    pipe._state = PipelineState.ACTIVE

    def _explode() -> None:
        raise RuntimeError("the hangup path is on fire")

    pipe.request_hangup = _explode  # type: ignore[method-assign]

    assert pipe.start_dictation() is False, (
        "a press that could not even ask for the microphone is a plain refusal, "
        "so the key-down latch is dropped and the next press is a fresh attempt"
    )
    await _drain_bus()

    assert [e.reason for e in seen.refused] == ["handover_failed"]
    assert seen.refused[0].detail.strip()
    assert pipe._dictation_task is None
    assert pipe._dictation_handover_task is None
    assert pipe._activation_allowed() is True


@pytest.mark.asyncio
async def test_releasing_the_key_mid_handover_records_nothing_and_says_why() -> None:
    """A hold gesture let go before the microphone came back.

    Starting the recording anyway would leave a dictation nobody is holding a
    key for, running to the duration cap — the exact reason the old code refused
    outright. Cancelling it is silent unless it explains itself.

    Released here in the SAME event-loop step as the press, so the waiting task
    is cancelled before it has run a single line. That is a normal human gesture
    and it is where a plain ``except asyncio.CancelledError`` inside the task
    would have quietly explained nothing.
    """
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)
    session = _run_voice_session(pipe)
    await asyncio.sleep(0)

    pipe._on_dictate_press()
    assert pipe._dictate_key_down is True
    handover = _handover_of(pipe)

    pipe._on_dictate_release()
    with pytest.raises(asyncio.CancelledError):
        await handover
    await _drain_bus()

    assert [e.reason for e in seen.refused] == ["handover_failed"]
    assert "press the key again" in seen.refused[0].detail.lower()
    assert seen.started == []
    assert pipe._dictation_task is None
    assert pipe._activation_allowed() is True

    # The hangup itself DID go through, which is what makes the next press work.
    await asyncio.wait_for(session, timeout=2.0)
    assert pipe._state is PipelineState.IDLE
    assert pipe.start_dictation() is True
    await _end_dictation(pipe)


# --------------------------------------------------------------------------
# The wake gate across the handover
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wake_stays_muted_for_the_whole_handover_and_the_dictation() -> None:
    """The gap between "session ended" and "dictation recording" is wake's
    steady state — a wake word firing there would take the microphone straight
    back from the user who just pressed the key."""
    bus = EventBus()
    _Collector(bus)
    pipe = _pipeline(bus)
    session = _run_voice_session(pipe)
    await asyncio.sleep(0)

    assert pipe.start_dictation() is True
    handover = _handover_of(pipe)
    assert pipe._dictation_blocks_activation() is True, "closed at the key press"
    assert pipe._activation_allowed() is False

    await asyncio.wait_for(session, timeout=2.0)
    await asyncio.wait_for(handover, timeout=2.0)
    assert pipe._dictation_blocks_activation() is True, "still closed while recording"

    await _end_dictation(pipe)
    assert pipe._dictation_blocks_activation() is False
    assert pipe._activation_allowed() is True


@pytest.mark.asyncio
async def test_the_handover_block_expires_even_if_the_task_is_killed_outright() -> None:
    """Nothing runs a ``finally`` here — the watchdog deadline is the backstop."""
    bus = EventBus()
    _Collector(bus)
    pipe = _pipeline(bus)
    pipe._state = PipelineState.ACTIVE

    assert pipe.start_dictation() is True
    handover = pipe._dictation_handover_task
    assert handover is not None
    assert pipe._activation_allowed() is False

    handover.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handover

    assert pipe._dictation_blocks_activation() is False
    assert pipe._activation_allowed() is True


# --------------------------------------------------------------------------
# Two presses, one lane
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_press_during_a_handover_is_the_already_running_no_op() -> None:
    """Otherwise two handovers race for one microphone."""
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)
    session = _run_voice_session(pipe)
    await asyncio.sleep(0)

    assert pipe.start_dictation() is True
    first = _handover_of(pipe)
    assert pipe.start_dictation() is False
    assert pipe._dictation_handover_task is first
    await _drain_bus()

    assert [e.reason for e in seen.refused] == ["already_running"]

    await asyncio.wait_for(session, timeout=2.0)
    await asyncio.wait_for(first, timeout=2.0)
    await _end_dictation(pipe)


@pytest.mark.asyncio
async def test_the_hands_free_toggle_cancels_a_pending_handover() -> None:
    bus = EventBus()
    _Collector(bus)
    pipe = _pipeline(bus)
    pipe._dictation_cfg = None
    session = _run_voice_session(pipe)
    await asyncio.sleep(0)

    pipe._on_dictate_toggle()
    handover = pipe._dictation_handover_task
    assert handover is not None

    pipe._on_dictate_toggle()
    with pytest.raises(asyncio.CancelledError):
        await handover
    assert pipe._dictation_task is None

    await asyncio.wait_for(session, timeout=2.0)


# --------------------------------------------------------------------------
# Vocabulary (AP-4: the token crosses pipeline → bus → REST → UI)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_handover_refusal_uses_the_shared_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_mod, "_DICTATION_HANDOVER_TIMEOUT_S", 0.0)
    bus = EventBus()
    seen = _Collector(bus)
    pipe = _pipeline(bus)
    pipe._state = PipelineState.ACTIVE

    assert pipe.start_dictation() is True
    handover = pipe._dictation_handover_task
    assert handover is not None
    await asyncio.wait_for(handover, timeout=2.0)
    await _drain_bus()

    assert seen.refused
    for event in seen.refused:
        assert event.reason in DICTATION_REFUSAL_REASONS
        assert event.detail.strip(), "a refusal without a sentence is not observable"
