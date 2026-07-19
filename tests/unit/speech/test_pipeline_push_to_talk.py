"""Unit tests for the push-to-talk recording path.

True push-to-talk (user mandate 2026-05-29): holding the configured hotkey
records, releasing it submits the captured audio as ONE prompt (one-shot).
These tests lock the two halves of that contract:

* ``_on_ptt_press`` / ``_on_ptt_release`` — the arming + edge handlers, which
  must be idempotent against ``global_hotkeys`` key-repeat and must never start
  a recording while another session is running.
* ``_ptt_session`` — the raw-capture loop that bypasses the VAD: the key, not
  silence detection, is the endpoint. It records every mic chunk until release
  (or hangup / max-hold), then submits the buffer to ``_handle_utterance``.

The microphone is faked (no audio hardware on CI / the cloud-first VPS path):
``_FakeMic`` yields a fixed list of chunks, then blocks open until cancelled,
exactly like a live mic between the last word and the key release.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace

from jarvis.core.events import TranscriptionUpdate
from jarvis.core.protocols import AudioChunk
from jarvis.sessions.constants import HANGUP_HOTKEY, HANGUP_TURN_COMPLETE
from jarvis.speech.pipeline import PipelineState, SpeechPipeline, TurnTakingState


class FakeTTS:
    name = "fake-tts"
    supports_streaming = False

    async def synthesize(  # type: ignore[no-untyped-def]
        self, text: str, language_code=None
    ) -> AsyncIterator[bytes]:  # pragma: no cover
        if False:
            yield b""


class _FakeMic:
    """Async-context mic stand-in: yields ``chunks`` then stays open."""

    def __init__(self, device=None, chunks: list[bytes] | None = None) -> None:
        self._chunks = chunks or []

    async def __aenter__(self) -> _FakeMic:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def stream(self) -> AsyncIterator[AudioChunk]:
        for pcm in self._chunks:
            yield AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=0, channels=1)
            await asyncio.sleep(0)
        # Mic stays open after the last chunk — the user is still holding the
        # key (maybe mid-thought). Block until the drain task is cancelled.
        await asyncio.Event().wait()


def _make_pipeline() -> SpeechPipeline:
    pipe = SpeechPipeline(tts=FakeTTS(), bus=None, enable_whisper_wake=False)
    pipe._state = PipelineState.IDLE
    pipe._activation_allowed = lambda: True  # type: ignore[method-assign]
    return pipe


def _silence_side_effects(pipe: SpeechPipeline) -> None:
    async def _noop_state(state):  # noqa: ANN001
        return None

    async def _noop_event(evt):  # noqa: ANN001
        return None

    async def _noop_captured(pcm):  # noqa: ANN001
        return None

    pipe._set_turn_state = _noop_state  # type: ignore[method-assign]
    pipe._publish_event = _noop_event  # type: ignore[method-assign]
    pipe._publish_utterance_captured = _noop_captured  # type: ignore[method-assign]


# ----------------------------------------------------------------------
# Edge handlers — arming
# ----------------------------------------------------------------------

def test_ptt_press_from_idle_arms_and_opens_session():
    pipe = _make_pipeline()
    assert not pipe._ptt_mode
    pipe._on_ptt_press()
    assert pipe._ptt_mode is True
    assert pipe._call_event.is_set()
    assert not pipe._ptt_release_event.is_set()


def test_ptt_press_ignored_when_a_session_is_active():
    pipe = _make_pipeline()
    pipe._state = PipelineState.ACTIVE
    pipe._on_ptt_press()
    assert pipe._ptt_mode is False
    assert not pipe._call_event.is_set()


def test_ptt_press_is_idempotent_against_key_repeat():
    """global_hotkeys re-fires on_press on every poll while the chord is held.
    A second press while already armed must not re-clear the release event or
    re-set the session."""
    pipe = _make_pipeline()
    pipe._on_ptt_press()
    pipe._ptt_release_event.set()  # pretend a release slipped in
    pipe._on_ptt_press()  # key-repeat — must be a no-op
    assert pipe._ptt_release_event.is_set()  # not cleared by the repeat


def test_ptt_press_blocked_when_activation_gate_closed():
    pipe = _make_pipeline()
    pipe._activation_allowed = lambda: False  # type: ignore[method-assign]
    pipe._on_ptt_press()
    assert pipe._ptt_mode is False


def test_ptt_release_is_noop_when_not_armed():
    pipe = _make_pipeline()
    pipe._on_ptt_release()
    assert not pipe._ptt_release_event.is_set()


def test_ptt_release_signals_when_armed():
    pipe = _make_pipeline()
    pipe._on_ptt_press()
    pipe._on_ptt_release()
    assert pipe._ptt_release_event.is_set()


# ----------------------------------------------------------------------
# Recording loop
# ----------------------------------------------------------------------

# 100 ms of 16 kHz mono int16 = 1600 samples = 3200 bytes.
_CHUNK_100MS = b"\x10\x00" * 1600


async def test_ptt_session_records_until_release_and_submits(monkeypatch):
    pipe = _make_pipeline()
    _silence_side_effects(pipe)
    pipe._ptt_mode = True
    # 5 chunks = 500 ms — comfortably over the 300 ms min-hold gate.
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None, **kwargs: _FakeMic(chunks=[_CHUNK_100MS] * 5),
    )
    captured: dict[str, object] = {}

    async def fake_handle(pcm: bytes, *, skip_completion: bool = False) -> bool:
        captured["pcm"] = pcm
        captured["skip_completion"] = skip_completion
        return False

    pipe._handle_utterance = fake_handle  # type: ignore[method-assign]

    async def _release_soon() -> None:
        await asyncio.sleep(0.15)
        pipe._on_ptt_release()

    asyncio.create_task(_release_soon())
    reason = await asyncio.wait_for(pipe._ptt_session(), timeout=2.0)

    assert reason == HANGUP_TURN_COMPLETE
    assert captured.get("pcm") == _CHUNK_100MS * 5, "held audio submitted on release"
    # PTT must bypass the incomplete-sentence buffer — the key release is the
    # endpoint, so the turn goes straight to the brain (no flush-timer delay).
    assert captured.get("skip_completion") is True


async def test_ptt_session_short_tap_submits_nothing(monkeypatch):
    """An accidental tap (sub-300 ms) carries no real speech — no brain turn."""
    pipe = _make_pipeline()
    _silence_side_effects(pipe)
    pipe._ptt_mode = True
    # One 100 ms chunk, then immediate release → below the 300 ms gate.
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None, **kwargs: _FakeMic(chunks=[_CHUNK_100MS]),
    )
    handled = {"called": False}

    async def fake_handle(pcm: bytes, *, skip_completion: bool = False) -> bool:
        handled["called"] = True
        return False

    pipe._handle_utterance = fake_handle  # type: ignore[method-assign]
    pipe._ptt_release_event.set()  # released before any meaningful audio
    reason = await asyncio.wait_for(pipe._ptt_session(), timeout=2.0)

    assert reason == HANGUP_TURN_COMPLETE
    assert handled["called"] is False


async def test_ptt_session_hangup_during_hold_returns_hotkey(monkeypatch):
    pipe = _make_pipeline()
    _silence_side_effects(pipe)
    pipe._ptt_mode = True
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None, **kwargs: _FakeMic(chunks=[_CHUNK_100MS] * 3),
    )
    handled = {"called": False}

    async def fake_handle(pcm: bytes, *, skip_completion: bool = False) -> bool:
        handled["called"] = True
        return False

    pipe._handle_utterance = fake_handle  # type: ignore[method-assign]

    async def _hangup_soon() -> None:
        await asyncio.sleep(0.15)
        pipe._hangup_event.set()

    asyncio.create_task(_hangup_soon())
    reason = await asyncio.wait_for(pipe._ptt_session(), timeout=2.0)

    assert reason == HANGUP_HOTKEY
    assert handled["called"] is False, "a hangup mid-hold discards the capture"


# ----------------------------------------------------------------------
# State-loop integration — a discarded call must not strand _ptt_mode
# ----------------------------------------------------------------------

async def _pump_state_loop_once(pipe: SpeechPipeline) -> None:
    """Run the state loop just long enough to consume one call event, then
    cancel (the loop re-blocks on the next ``_call_event.wait()``)."""
    task = asyncio.create_task(pipe._state_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_wake_lock_discards_call_and_clears_ptt_mode():
    """A PTT press whose call lands during the post-hangup wake-lock is
    dropped by the state loop — and must NOT leave ``_ptt_mode`` armed, or the
    next wake-word call would wrongly enter the keyless raw-recording path."""
    pipe = _make_pipeline()
    pipe._wake_lock_until = time.time() + 100.0  # lock wide open
    pipe._ptt_mode = True
    pipe._call_event.set()

    await _pump_state_loop_once(pipe)

    assert pipe._ptt_mode is False
    assert pipe._state == PipelineState.IDLE


async def test_closed_activation_gate_discards_call_and_clears_ptt_mode():
    pipe = _make_pipeline()
    pipe._activation_allowed = lambda: False  # type: ignore[method-assign]
    pipe._ptt_mode = True
    pipe._call_event.set()

    await _pump_state_loop_once(pipe)

    assert pipe._ptt_mode is False
    assert pipe._state == PipelineState.IDLE


# ----------------------------------------------------------------------
# ACK — PTT must NOT run the 400 ms echo dead-zone before the mic opens
# ----------------------------------------------------------------------

class _RecordingPlayer:
    def __init__(self) -> None:
        self.plays: list[int] = []

    async def play_pcm(self, pcm, sample_rate=None):  # noqa: ANN001
        self.plays.append(sample_rate or 0)


async def test_ptt_ack_plays_chime_only_without_dead_zone(monkeypatch):
    """REGRESSION: ``_play_ack(ptt=True)`` must play the chime and return — no
    spoken ACK and, critically, NO 400 ms ``asyncio.sleep``. The dead-zone runs
    BEFORE the PTT mic opens, so leaving it in swallows the opening words of
    every capture and turns a short hold into a silent no-op."""
    pipe = _make_pipeline()
    pipe._player = _RecordingPlayer()  # type: ignore[assignment]
    pipe._ack_pcm = b"\x00\x00" * 200  # a non-empty spoken-ACK clip

    slept: list[float] = []

    async def _fake_sleep(secs: float) -> None:
        slept.append(secs)

    monkeypatch.setattr("jarvis.speech.pipeline.asyncio.sleep", _fake_sleep)

    await pipe._play_ack(ptt=True)
    assert len(pipe._player.plays) == 1, "PTT must play the chime only"
    assert 0.4 not in slept, "PTT must NOT run the echo dead-zone"


class _FakeSTT:
    """Minimal STT stand-in that returns a fixed transcript for the live feed."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes):  # noqa: ANN201
        self.calls += 1
        return SimpleNamespace(text=self._text, language="de", confidence=0.9)


async def test_ptt_publishes_live_partials_while_holding(monkeypatch):
    """While the key is held, PTT must re-transcribe the growing buffer and
    publish non-final TranscriptionUpdate events so the orb bubble shows the
    live transcript (parity with the wake-word probe). Without this the user
    sees an empty bubble the whole time they speak."""
    pipe = _make_pipeline()
    pipe._ptt_mode = True
    pipe._ptt_partial_interval_s = 0.05  # fast feed for the test
    pipe._utterance_stt = _FakeSTT("hallo welt")  # type: ignore[assignment]

    published: list = []

    async def _cap(evt):  # noqa: ANN001
        published.append(evt)

    async def _noop_state(state):  # noqa: ANN001
        return None

    async def _noop_captured(pcm):  # noqa: ANN001
        return None

    async def _fake_handle(pcm: bytes, *, skip_completion: bool = False) -> bool:
        return False

    pipe._publish_event = _cap  # type: ignore[method-assign]
    pipe._set_turn_state = _noop_state  # type: ignore[method-assign]
    pipe._publish_utterance_captured = _noop_captured  # type: ignore[method-assign]
    pipe._handle_utterance = _fake_handle  # type: ignore[method-assign]
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None, **kwargs: _FakeMic(chunks=[_CHUNK_100MS] * 10),  # ~1s buffered
    )

    async def _release_later() -> None:
        await asyncio.sleep(0.2)  # several 50 ms intervals → ≥1 partial
        pipe._on_ptt_release()

    asyncio.create_task(_release_later())
    await asyncio.wait_for(pipe._ptt_session(), timeout=2.0)

    partials = [
        e for e in published
        if isinstance(e, TranscriptionUpdate) and not e.is_final
    ]
    assert partials, "PTT must publish a live (non-final) transcript while holding"
    assert partials[0].text == "hallo welt"
    assert pipe._utterance_stt.calls >= 1


async def test_ptt_live_feed_disabled_when_interval_zero(monkeypatch):
    """Setting the interval to 0 turns the live feed off (cost escape hatch)."""
    pipe = _make_pipeline()
    pipe._ptt_mode = True
    pipe._ptt_partial_interval_s = 0.0
    pipe._utterance_stt = _FakeSTT("nope")  # type: ignore[assignment]
    _silence_side_effects(pipe)

    async def _fake_handle(pcm: bytes, *, skip_completion: bool = False) -> bool:
        return False

    pipe._handle_utterance = _fake_handle  # type: ignore[method-assign]
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None, **kwargs: _FakeMic(chunks=[_CHUNK_100MS] * 10),
    )

    async def _release_later() -> None:
        await asyncio.sleep(0.15)
        pipe._on_ptt_release()

    asyncio.create_task(_release_later())
    await asyncio.wait_for(pipe._ptt_session(), timeout=2.0)
    assert pipe._utterance_stt.calls == 0, "interval=0 must not probe the STT"


class _SerialProbeSTT:
    """STT fake that exposes any overlap between partial and final calls."""

    def __init__(self) -> None:
        self.partial_started = asyncio.Event()
        self.finish_partial = asyncio.Event()
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def transcribe_pcm(self, pcm: bytes):  # noqa: ANN201
        self.calls += 1
        call = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if call == 1:
                self.partial_started.set()
                await self.finish_partial.wait()
                return SimpleNamespace(text="partial", language="en", confidence=0.9)
            return SimpleNamespace(text="final", language="en", confidence=0.9)
        finally:
            self.active -= 1


class _TrackedInputBuffer:
    """Wake-handoff stand-in that records exactly when PTT releases capture."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = asyncio.Event()

    async def stream(self) -> AsyncIterator[AudioChunk]:
        for pcm in self._chunks:
            yield AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=0, channels=1)
            await asyncio.sleep(0)
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()


async def test_ptt_release_closes_capture_then_waits_for_live_probe_before_final_stt():
    """A native live probe must quiesce before final STT uses the same model."""
    pipe = _make_pipeline()
    _silence_side_effects(pipe)
    pipe._ptt_mode = True
    pipe._ptt_partial_interval_s = 0.01
    pipe._stt_final_timeout_s = 0.5
    stt = _SerialProbeSTT()
    pipe._utterance_stt = stt  # type: ignore[assignment]
    input_buffer = _TrackedInputBuffer([_CHUNK_100MS] * 8)
    states: list[TurnTakingState] = []

    async def _track_state(state: TurnTakingState) -> None:
        states.append(state)

    pipe._set_turn_state = _track_state  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    async def _finalize(pcm: bytes, *, skip_completion: bool = False) -> bool:
        captured["transcript"] = await pipe._transcribe_final(pcm)
        return False

    pipe._handle_utterance = _finalize  # type: ignore[method-assign]
    session = asyncio.create_task(pipe._ptt_session(input_buffer=input_buffer))
    await asyncio.wait_for(stt.partial_started.wait(), timeout=1.0)

    pipe._on_ptt_release()
    await asyncio.wait_for(input_buffer.closed.wait(), timeout=0.2)
    assert states[-1] is TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT
    await asyncio.sleep(0.03)
    assert stt.calls == 1, "final STT started while the partial still owned the model"

    stt.finish_partial.set()
    reason = await asyncio.wait_for(session, timeout=2.0)

    assert reason == HANGUP_TURN_COMPLETE
    assert stt.calls == 2
    assert stt.max_active == 1
    assert getattr(captured["transcript"], "text", "") == "final"


class _RecoverableBlockingSTT:
    """Models an un-cancellable native probe and its fresh-engine recovery."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.recover_calls = 0

    async def transcribe_pcm(self, pcm: bytes):  # noqa: ANN201
        self.started.set()
        await asyncio.Event().wait()

    def recover(self) -> None:
        self.recover_calls += 1


class _RecoverableNativeSTT:
    """Models a timed-out ``to_thread`` probe that survives task cancellation."""

    def __init__(self) -> None:
        self.partial_started = asyncio.Event()
        self.release_native_call = threading.Event()
        self.calls = 0
        self.recover_calls = 0
        self.final_started_after_recover = False

    async def transcribe_pcm(self, pcm: bytes):  # noqa: ANN201
        self.calls += 1
        if self.calls == 1:
            self.partial_started.set()
            await asyncio.to_thread(self.release_native_call.wait)
            return SimpleNamespace(text="partial", language="en", confidence=0.9)
        self.final_started_after_recover = self.recover_calls > 0
        return SimpleNamespace(text="final", language="en", confidence=0.9)

    def recover(self) -> None:
        self.recover_calls += 1


async def test_ptt_release_recovers_timed_out_native_probe_before_final_stt(
    monkeypatch,
):
    """A wedged cosmetic probe is orphaned onto a fresh engine before submit."""
    pipe = _make_pipeline()
    _silence_side_effects(pipe)
    pipe._ptt_mode = True
    pipe._ptt_partial_interval_s = 0.01
    pipe._stt_final_timeout_s = 0.05
    stt = _RecoverableNativeSTT()
    pipe._utterance_stt = stt  # type: ignore[assignment]
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None, **kwargs: _FakeMic(chunks=[_CHUNK_100MS] * 8),
    )
    captured: dict[str, object] = {}

    async def _finalize(pcm: bytes, *, skip_completion: bool = False) -> bool:
        captured["transcript"] = await pipe._transcribe_final(pcm)
        return False

    pipe._handle_utterance = _finalize  # type: ignore[method-assign]
    session = asyncio.create_task(pipe._ptt_session())
    try:
        await asyncio.wait_for(stt.partial_started.wait(), timeout=1.0)
        pipe._on_ptt_release()
        reason = await asyncio.wait_for(session, timeout=2.0)
    finally:
        # Let the deliberately orphaned executor call leave its old engine.
        stt.release_native_call.set()
        if not session.done():
            session.cancel()
            try:
                await session
            except asyncio.CancelledError:  # noqa: S110 - test cleanup
                pass

    assert reason == HANGUP_TURN_COMPLETE
    assert stt.recover_calls == 1
    assert stt.calls == 2
    assert stt.final_started_after_recover is True
    assert getattr(captured["transcript"], "text", "") == "final"


async def test_ptt_hangup_recovers_live_probe_before_wake_rearm(monkeypatch):
    """A hard hangup never leaves a busy native STT engine for the wake path."""
    pipe = _make_pipeline()
    _silence_side_effects(pipe)
    pipe._ptt_mode = True
    pipe._ptt_partial_interval_s = 0.01
    stt = _RecoverableBlockingSTT()
    pipe._utterance_stt = stt  # type: ignore[assignment]
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None, **kwargs: _FakeMic(chunks=[_CHUNK_100MS] * 8),
    )

    session = asyncio.create_task(pipe._ptt_session())
    await asyncio.wait_for(stt.started.wait(), timeout=1.0)
    pipe._hangup_event.set()
    reason = await asyncio.wait_for(session, timeout=2.0)

    assert reason == HANGUP_HOTKEY
    assert stt.recover_calls == 1


async def test_non_ptt_ack_plays_spoken_ack_and_dead_zone(monkeypatch):
    """The wake-word path keeps the spoken 'Ja?' + the 400 ms dead-zone."""
    pipe = _make_pipeline()
    pipe._player = _RecordingPlayer()  # type: ignore[assignment]
    pipe._ack_pcm = b"\x00\x00" * 200

    slept: list[float] = []

    async def _fake_sleep(secs: float) -> None:
        slept.append(secs)

    monkeypatch.setattr("jarvis.speech.pipeline.asyncio.sleep", _fake_sleep)

    await pipe._play_ack(ptt=False)
    assert len(pipe._player.plays) == 2, "wake path plays chime + spoken ACK"
    assert 0.4 in slept, "wake path keeps the echo dead-zone"
