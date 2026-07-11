"""Capture-first voice-session startup regression coverage.

The native Jarvis Bar is allowed to advertise LISTENING only after microphone
capture is armed. Audio that arrives while start subscribers or a realtime
provider starts must be buffered and delivered to the selected voice engine,
not discarded before ``_active_session`` begins consuming it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.events import (
    VoiceSessionEnded,
    VoiceSessionStarted,
    WakeCandidateDetected,
    WakeWordDetected,
)
from jarvis.core.protocols import AudioChunk
from jarvis.sessions.constants import HANGUP_TURN_COMPLETE
from jarvis.speech.pipeline import PipelineState, SpeechPipeline, TurnTakingState


class _FakeTTS:
    name = "fake-tts"
    supports_streaming = False

    async def synthesize(self, text: str, **_kwargs) -> AsyncIterator[bytes]:
        if False:  # pragma: no cover - protocol-shaped empty iterator
            yield text.encode()


class _ControlledMic:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.frames: asyncio.Queue[AudioChunk] = asyncio.Queue()
        self.entered = asyncio.Event()
        self.open_count = 0
        self.close_count = 0
        self.closed = False

    async def __aenter__(self) -> _ControlledMic:
        self.open_count += 1
        self.order.append("mic_open")
        self.entered.set()
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        self.closed = True
        self.close_count += 1
        self.order.append("mic_close")
        return False

    async def stream(self) -> AsyncIterator[AudioChunk]:
        while True:
            yield await self.frames.get()


class _NeverWake:
    async def detect(self, chunks: AsyncIterator[AudioChunk]) -> AsyncIterator[str]:
        async for _chunk in chunks:
            await asyncio.Event().wait()
        if False:  # pragma: no cover - protocol-shaped async iterator
            yield ""


class _OneHitWake:
    async def detect(self, chunks: AsyncIterator[AudioChunk]) -> AsyncIterator[str]:
        await anext(chunks)
        yield "jarvis"


def _handoff_pipeline() -> SpeechPipeline:
    return SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)


@pytest.mark.asyncio
async def test_capture_precedes_listening_signal_and_preserves_startup_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first frame after the bar appears must reach the voice engine.

    This pins the complete ordering contract that the prior realtime-only
    preroll missed: microphone open -> VoiceSessionStarted/LISTENING -> active
    engine. A frame injected while start subscribers are still running is the
    user's opening word and must be delivered unchanged through the buffer.
    """
    order: list[str] = []
    mic = _ControlledMic(order)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: mic)

    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._activation_allowed = lambda: True  # type: ignore[method-assign]

    ended = asyncio.Event()
    captured: list[bytes] = []

    async def _publish(event) -> None:  # noqa: ANN001
        if isinstance(event, VoiceSessionStarted):
            order.append("session_started")
            assert order[0] == "mic_open"
            await mic.frames.put(
                AudioChunk(
                    pcm=b"opening-word",
                    sample_rate=16_000,
                    timestamp_ns=1,
                )
            )
            await asyncio.sleep(0)
        elif isinstance(event, VoiceSessionEnded):
            ended.set()

    async def _set_turn_state(state, **_kwargs) -> None:  # noqa: ANN001
        if state is TurnTakingState.LISTENING:
            order.append("listening")

    async def _play_ack(*, ptt: bool) -> None:
        order.append("ack")

    async def _active_session(*, input_buffer=None) -> str:  # noqa: ANN001
        order.append("active_session")
        assert input_buffer is not None
        chunk = await asyncio.wait_for(anext(input_buffer.stream()), timeout=0.5)
        captured.append(chunk.pcm)
        return HANGUP_TURN_COMPLETE

    async def _play_earcon(*_args, **_kwargs) -> None:
        return None

    pipeline._publish_event = _publish  # type: ignore[method-assign]
    pipeline._set_turn_state = _set_turn_state  # type: ignore[method-assign]
    pipeline._play_ack = _play_ack  # type: ignore[method-assign]
    pipeline._active_session = _active_session  # type: ignore[method-assign]
    pipeline._play_earcon = _play_earcon  # type: ignore[method-assign]
    pipeline._call_event.set()

    state_task = asyncio.create_task(pipeline._state_loop())
    try:
        await asyncio.wait_for(ended.wait(), timeout=1.0)
    finally:
        state_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state_task

    assert captured == [b"opening-word"]
    assert order.index("mic_open") < order.index("session_started")
    assert order.index("session_started") < order.index("active_session")
    assert "ack" not in order
    assert mic.closed is True


@pytest.mark.asyncio
async def test_replay_buffer_never_silently_drops_the_command_prefix() -> None:
    """Crossing the byte cap must fail honestly, not start from a tail frame."""
    buffer = pipeline_mod._SessionInputBuffer(max_buffer_bytes=4)  # noqa: SLF001
    buffer.put(AudioChunk(pcm=b"aaaa", sample_rate=16_000, timestamp_ns=1))
    buffer.put(AudioChunk(pcm=b"bbbb", sample_rate=16_000, timestamp_ns=2))

    with pytest.raises(RuntimeError, match="refusing to drop the command prefix"):
        await anext(buffer.stream())


@pytest.mark.asyncio
async def test_replay_buffer_can_restart_for_realtime_to_pipeline_fallback() -> None:
    """A failed realtime consumer must leave the opening available to VAD."""
    marker = AudioChunk(pcm=b"opening", sample_rate=16_000, timestamp_ns=1)
    buffer = pipeline_mod._SessionInputBuffer(initial=(marker,))  # noqa: SLF001

    first = buffer.stream()
    assert await anext(first) is marker
    await first.aclose()

    fallback = buffer.stream()
    assert await anext(fallback) is marker
    await fallback.aclose()


@pytest.mark.asyncio
async def test_live_wake_capture_is_reused_without_second_microphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wake and active capture share one physical microphone lease."""
    mic = _ControlledMic([])
    factory_calls = 0

    def _mic_factory(**_kwargs) -> _ControlledMic:
        nonlocal factory_calls
        factory_calls += 1
        return mic

    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _mic_factory)
    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._openwakeword_enabled = True
    pipeline._whisper_wake_enabled = False
    pipeline._wake = _NeverWake()  # type: ignore[assignment]

    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=0.5)

    marker = AudioChunk(pcm=b"first-word", sample_rate=16_000, timestamp_ns=1)
    async with pipeline._capture_first_session_input() as input_buffer:
        assert factory_calls == 1
        assert mic.closed is False
        await mic.frames.put(marker)
        assert await asyncio.wait_for(anext(input_buffer.stream()), timeout=0.5) is marker

    await asyncio.wait_for(wake_task, timeout=0.5)
    assert factory_calls == 1
    assert mic.open_count == 1
    assert mic.close_count == 1
    assert pipeline._wake_capture_released.is_set()


@pytest.mark.asyncio
async def test_candidate_close_cancels_verification_and_retracts_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The visible X cancels a candidate instead of starting a voice turn."""
    mic = _ControlledMic([])
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: mic)
    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._openwakeword_enabled = True
    pipeline._whisper_wake_enabled = False
    pipeline._wake = _OneHitWake()  # type: ignore[assignment]
    pipeline._should_show_optimistic_candidate = lambda: True  # type: ignore[method-assign]

    verify_entered = asyncio.Event()
    verify_cancelled = asyncio.Event()
    candidate_flags: list[bool] = []
    authoritative_wakes: list[WakeWordDetected] = []

    async def _verify(_pcm: bytes) -> bool:
        verify_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            verify_cancelled.set()
        return True

    async def _publish(event) -> None:  # noqa: ANN001
        if isinstance(event, WakeCandidateDetected):
            candidate_flags.append(event.active)
        elif isinstance(event, WakeWordDetected):
            authoritative_wakes.append(event)

    pipeline._verify_oww_hit = _verify  # type: ignore[method-assign]
    pipeline._publish_event = _publish  # type: ignore[method-assign]
    await mic.frames.put(
        AudioChunk(pcm=b"candidate", sample_rate=16_000, timestamp_ns=1)
    )

    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(verify_entered.wait(), timeout=0.5)
    assert pipeline.is_session_active() is True
    pipeline.request_hangup()
    await asyncio.wait_for(wake_task, timeout=0.5)

    assert verify_cancelled.is_set()
    assert candidate_flags == [True, False]
    assert authoritative_wakes == []
    assert pipeline._call_event.is_set() is False
    assert pipeline._hangup_event.is_set() is False
    assert pipeline._external_hangup_pending.is_set() is False
    assert pipeline._wake_cancel_event.is_set() is False


@pytest.mark.asyncio
async def test_gate_drop_releases_unoffered_wake_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected activation cannot leave the wake microphone detector-dead."""
    mic = _ControlledMic([])
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: mic)
    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._openwakeword_enabled = True
    pipeline._whisper_wake_enabled = False
    pipeline._wake = _NeverWake()  # type: ignore[assignment]
    pipeline._activation_allowed = lambda: False  # type: ignore[method-assign]
    started: list[VoiceSessionStarted] = []
    candidate_flags: list[bool] = []

    async def _publish(event) -> None:  # noqa: ANN001
        if isinstance(event, VoiceSessionStarted):
            started.append(event)
        elif isinstance(event, WakeCandidateDetected):
            candidate_flags.append(event.active)

    pipeline._publish_event = _publish  # type: ignore[method-assign]
    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=0.5)
    state_task = asyncio.create_task(pipeline._state_loop())
    pipeline._call_event.set()
    try:
        await asyncio.wait_for(wake_task, timeout=0.5)
    finally:
        state_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state_task

    assert started == []
    assert candidate_flags == [False]
    assert pipeline._state is PipelineState.IDLE
    assert mic.close_count == 1
    assert pipeline._wake_capture_released.is_set()


@pytest.mark.asyncio
async def test_close_after_confirmed_handoff_survives_state_loop_start() -> None:
    """A close in the wake-event dispatch window must not be cleared as stale."""
    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._activation_allowed = lambda: True  # type: ignore[method-assign]
    buffer = pipeline_mod._SessionInputBuffer()  # noqa: SLF001
    pipeline._wake_capture_released.clear()
    pipeline._wake_handoff_buffer = buffer
    pipeline._wake_handoff_ready.set()
    ended = asyncio.Event()
    active_calls = 0
    hard_hangup_flags: list[bool] = []

    async def _wake_owner() -> None:
        await buffer.released.wait()
        pipeline._wake_capture_released.set()

    async def _publish(event) -> None:  # noqa: ANN001
        if isinstance(event, VoiceSessionEnded):
            ended.set()

    async def _active_session(*, input_buffer=None) -> str:  # noqa: ANN001
        nonlocal active_calls
        active_calls += 1
        return HANGUP_TURN_COMPLETE

    async def _play_earcon(*_args, **_kwargs) -> None:
        return None

    def _post_hangup_lock_seconds() -> float:
        hard_hangup_flags.append(pipeline._explicit_hard_hangup)
        return 0.0

    pipeline._publish_event = _publish  # type: ignore[method-assign]
    pipeline._active_session = _active_session  # type: ignore[method-assign]
    pipeline._play_earcon = _play_earcon  # type: ignore[method-assign]
    pipeline._post_hangup_lock_seconds = _post_hangup_lock_seconds  # type: ignore[method-assign]
    assert pipeline.is_session_active() is True
    pipeline.request_hangup()
    pipeline._call_event.set()
    owner_task = asyncio.create_task(_wake_owner())
    state_task = asyncio.create_task(pipeline._state_loop())
    try:
        await asyncio.wait_for(ended.wait(), timeout=0.5)
    finally:
        state_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state_task
        await owner_task

    assert active_calls == 0
    assert hard_hangup_flags == [True]
    assert pipeline._state is PipelineState.IDLE
    assert pipeline._wake_capture_released.is_set()


@pytest.mark.asyncio
async def test_stale_idle_hangup_does_not_cancel_a_later_wake_handoff() -> None:
    """Only a close after handoff belongs to the newly confirmed session."""
    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._activation_allowed = lambda: True  # type: ignore[method-assign]
    pipeline.request_hangup()
    assert pipeline._wake_handoff_hangup_pending.is_set() is False

    buffer = pipeline_mod._SessionInputBuffer()  # noqa: SLF001
    pipeline._wake_capture_released.clear()
    pipeline._wake_handoff_buffer = buffer
    pipeline._wake_handoff_ready.set()
    ended = asyncio.Event()
    active_calls = 0

    async def _wake_owner() -> None:
        await buffer.released.wait()
        pipeline._wake_capture_released.set()

    async def _publish(event) -> None:  # noqa: ANN001
        if isinstance(event, VoiceSessionEnded):
            ended.set()

    async def _active_session(*, input_buffer=None) -> str:  # noqa: ANN001
        nonlocal active_calls
        active_calls += 1
        return HANGUP_TURN_COMPLETE

    async def _play_earcon(*_args, **_kwargs) -> None:
        return None

    pipeline._publish_event = _publish  # type: ignore[method-assign]
    pipeline._active_session = _active_session  # type: ignore[method-assign]
    pipeline._play_earcon = _play_earcon  # type: ignore[method-assign]
    owner_task = asyncio.create_task(_wake_owner())
    state_task = asyncio.create_task(pipeline._state_loop())
    pipeline._call_event.set()
    try:
        await asyncio.wait_for(ended.wait(), timeout=0.5)
    finally:
        state_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state_task
        await owner_task

    assert active_calls == 1
    assert pipeline._state is PipelineState.IDLE


@pytest.mark.asyncio
async def test_claim_waits_for_wake_release_before_opening_fallback() -> None:
    """A closing wake stream must win the ownership race without overlap."""
    pipeline = _handoff_pipeline()
    pipeline._wake_capture_released.clear()

    async def _close_wake_capture() -> None:
        await pipeline._wake_stop_event.wait()
        pipeline._wake_capture_released.set()

    close_task = asyncio.create_task(_close_wake_capture())
    claimed = await asyncio.wait_for(
        pipeline._claim_wake_capture_for_session(),  # noqa: SLF001
        timeout=0.5,
    )
    await close_task

    assert claimed is None
    assert pipeline._wake_stop_event.is_set() is False


@pytest.mark.asyncio
async def test_claim_yields_to_wake_capture_entering_before_fallback() -> None:
    """A scheduled wake open must win over a simultaneous hotkey fallback."""
    pipeline = _handoff_pipeline()
    pipeline._wake_capture_released.set()
    offered = pipeline_mod._SessionInputBuffer()  # noqa: SLF001

    async def _enter_wake_capture() -> None:
        pipeline._wake_capture_released.clear()
        await pipeline._wake_stop_event.wait()
        pipeline._wake_handoff_buffer = offered
        pipeline._wake_handoff_ready.set()

    enter_task = asyncio.create_task(_enter_wake_capture())
    claimed = await asyncio.wait_for(
        pipeline._claim_wake_capture_for_session(),  # noqa: SLF001
        timeout=0.5,
    )
    await enter_task

    assert claimed is offered
    assert pipeline._wake_handoff_buffer is None
    assert pipeline._wake_handoff_ready.is_set() is False


@pytest.mark.asyncio
async def test_rejected_activation_releases_late_wake_handoff() -> None:
    """Rejecting a call must not orphan a handoff offered one tick later."""
    pipeline = _handoff_pipeline()
    pipeline._wake_capture_released.clear()
    offered = pipeline_mod._SessionInputBuffer()  # noqa: SLF001

    async def _offer_after_request() -> None:
        await pipeline._wake_stop_event.wait()
        pipeline._wake_handoff_buffer = offered
        pipeline._wake_handoff_ready.set()
        await offered.released.wait()
        pipeline._wake_capture_released.set()

    offer_task = asyncio.create_task(_offer_after_request())
    await asyncio.wait_for(
        pipeline._abort_pending_wake_handoff(),  # noqa: SLF001
        timeout=0.5,
    )
    await offer_task

    assert offered.released.is_set()
    assert pipeline._wake_handoff_buffer is None
    assert pipeline._wake_handoff_ready.is_set() is False


@pytest.mark.asyncio
async def test_manually_fed_buffer_finishes_without_hanging_consumer() -> None:
    """A wake fanout EOF must terminate the handed-off stream promptly."""
    marker = AudioChunk(pcm=b"opening", sample_rate=16_000, timestamp_ns=1)
    buffer = pipeline_mod._SessionInputBuffer(initial=(marker,))  # noqa: SLF001
    buffer.finish()
    stream = buffer.stream()

    assert await anext(stream) is marker
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_buffer_resumes_metering_between_engine_consumers() -> None:
    """Realtime teardown must not flatten the bar before classic fallback."""
    from jarvis.audio import mic_level

    marker = AudioChunk(pcm=b"\x00\x10" * 320, sample_rate=16_000, timestamp_ns=1)
    buffer = pipeline_mod._SessionInputBuffer(initial=(marker,))  # noqa: SLF001
    mic_level.reset_for_tests()
    levels: list[float] = []
    unsubscribe = mic_level.subscribe(levels.append)
    try:
        realtime = buffer.stream()
        assert await anext(realtime) is marker
        await realtime.aclose()

        buffer.put(
            AudioChunk(
                pcm=b"\x00\x40" * 320,
                sample_rate=16_000,
                timestamp_ns=2,
            )
        )
        assert levels and levels[-1] > 0.0
    finally:
        unsubscribe()
        mic_level.reset_for_tests()
