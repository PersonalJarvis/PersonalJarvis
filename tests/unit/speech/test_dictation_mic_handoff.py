"""One microphone owner at a time — a dictation takes the wake stream over.

The wake loop sits inside ``_run_parallel_wake`` with the input device open
whenever Jarvis is idle, which is exactly the state the dictation shortcut is
pressed in. Opening the dictation's own capture there meant TWO native input
streams on one device for the whole dictation: the BUG-014 family, and a shared
native engine between concurrent callers (AP-24).

So the dictation lane goes through the same single-owner handoff every other
session start uses. The tests that matter most are the ones about giving the
stream BACK: a dictation that ends normally, one that is cancelled mid-recording,
and one whose audio source dies under it must all leave the wake loop able to
re-arm its own microphone — a wake word that never comes back is BUG-037, whose
only cure is an app restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.config import DictationConfig
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import PipelineState, SpeechPipeline


class _FakeTTS:
    name = "fake-tts"
    supports_streaming = False

    async def synthesize(self, text: str, **_kwargs) -> AsyncIterator[bytes]:
        if False:  # pragma: no cover - protocol-shaped empty iterator
            yield text.encode()


class _StubSTT:
    """Returns nothing, so no test here depends on a transcription."""

    async def transcribe_pcm(self, pcm: bytes, **_kwargs):
        class _Empty:
            text = ""
            language = ""

        return _Empty()


class _ControlledMic:
    """A microphone whose open/close edges and frames the test drives."""

    def __init__(self) -> None:
        self.frames: asyncio.Queue[AudioChunk | BaseException] = asyncio.Queue()
        self.entered = asyncio.Event()
        self.open_count = 0
        self.close_count = 0
        self.closed = False

    async def __aenter__(self) -> _ControlledMic:
        self.open_count += 1
        self.closed = False
        self.entered.set()
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        self.closed = True
        self.close_count += 1
        return False

    async def stream(self) -> AsyncIterator[AudioChunk]:
        while True:
            item = await self.frames.get()
            if isinstance(item, BaseException):
                raise item
            yield item


class _NeverWake:
    async def detect(self, chunks: AsyncIterator[AudioChunk]) -> AsyncIterator[str]:
        async for _chunk in chunks:
            await asyncio.Event().wait()
        if False:  # pragma: no cover - protocol-shaped async iterator
            yield ""


def _chunk(pcm: bytes) -> AudioChunk:
    return AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=1)


def _wake_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SpeechPipeline, _ControlledMic, list[int]]:
    """A pipeline whose every ``MicrophoneCapture`` is the same fake device."""
    mic = _ControlledMic()
    factory_calls: list[int] = []

    def _mic_factory(**_kwargs) -> _ControlledMic:
        factory_calls.append(1)
        return mic

    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _mic_factory)
    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._openwakeword_enabled = True
    pipeline._whisper_wake_enabled = False
    pipeline._wake = _NeverWake()  # type: ignore[assignment]
    pipeline._utterance_stt = _StubSTT()
    # No live preview: this file is about the microphone lease, not transcription.
    pipeline._dictation_cfg = DictationConfig(partial_interval_s=0.0)
    pipeline._state = PipelineState.IDLE

    async def _no_delivery(**_kwargs) -> str:
        return ""

    pipeline._finish_dictation = _no_delivery  # type: ignore[assignment]
    return pipeline, mic, factory_calls


# --------------------------------------------------------------------------
# The handoff itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dictation_takes_the_live_wake_microphone_instead_of_opening_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, mic, factory_calls = _wake_pipeline(monkeypatch)

    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=1.0)
    assert len(factory_calls) == 1

    marker = _chunk(b"dictated-word")
    async with pipeline._capture_dictation_input() as source:
        # Still exactly one device open, and it is the wake loop's.
        assert len(factory_calls) == 1
        assert mic.closed is False
        await mic.frames.put(marker)
        assert await asyncio.wait_for(anext(source.stream()), timeout=1.0) is marker

    await asyncio.wait_for(wake_task, timeout=1.0)
    assert len(factory_calls) == 1
    assert mic.open_count == 1
    assert mic.close_count == 1
    assert pipeline._wake_capture_released.is_set()


@pytest.mark.asyncio
async def test_a_dictation_opens_one_capture_when_no_wake_stream_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headless / wake-parked case: nothing to take over, so open one."""
    pipeline, mic, factory_calls = _wake_pipeline(monkeypatch)
    assert pipeline._wake_capture_released.is_set()

    async with pipeline._capture_dictation_input() as source:
        assert len(factory_calls) == 1
        marker = _chunk(b"first-word")
        await mic.frames.put(marker)
        assert await asyncio.wait_for(anext(source.stream()), timeout=1.0) is marker

    assert mic.close_count == 1


@pytest.mark.asyncio
async def test_a_pipeline_without_wake_plumbing_still_dictates() -> None:
    """Unit-test pipelines built via ``__new__`` have no handoff events."""
    bare = SpeechPipeline.__new__(SpeechPipeline)
    assert await bare._claim_wake_capture_for_dictation() is None


# --------------------------------------------------------------------------
# Giving the stream back — the part a restart would otherwise be needed for
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_wake_loop_gets_its_microphone_back_after_a_dictation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full lane: start the dictation, stop it, and re-arm wake on one device."""
    pipeline, mic, factory_calls = _wake_pipeline(monkeypatch)

    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=1.0)

    assert pipeline.start_dictation(target="chat") is True
    dictation = pipeline._dictation_task
    assert dictation is not None
    # The wake stream is handed over, not duplicated.
    await asyncio.wait_for(pipeline._wake_handoff_ready.wait(), timeout=1.0)
    await mic.frames.put(_chunk(b"\x01\x02" * 8_000))
    await asyncio.sleep(0)
    assert len(factory_calls) == 1

    pipeline.stop_dictation()
    await asyncio.wait_for(dictation, timeout=2.0)
    await asyncio.wait_for(wake_task, timeout=1.0)

    assert mic.open_count == 1
    assert mic.close_count == 1
    assert pipeline._wake_capture_released.is_set()
    # And the gate that kept wake quiet is open again, so the next
    # ``_run_parallel_wake`` can re-open the device.
    assert pipeline._dictation_blocks_activation() is False

    mic.entered.clear()
    rearmed = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=1.0)
    assert mic.open_count == 2
    rearmed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await rearmed


@pytest.mark.asyncio
async def test_a_cancelled_dictation_still_releases_the_wake_microphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The crash path: nothing runs an orderly stop, and wake must still return."""
    pipeline, mic, _factory_calls = _wake_pipeline(monkeypatch)

    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=1.0)

    assert pipeline.start_dictation(target="chat") is True
    dictation = pipeline._dictation_task
    assert dictation is not None
    await asyncio.wait_for(pipeline._wake_handoff_ready.wait(), timeout=1.0)

    dictation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dictation

    await asyncio.wait_for(wake_task, timeout=1.0)
    assert mic.close_count == 1
    assert pipeline._wake_capture_released.is_set()
    assert pipeline._dictation_blocks_activation() is False


@pytest.mark.asyncio
async def test_a_dying_audio_source_ends_the_dictation_and_frees_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device disappears mid-dictation — both lanes have to unwind."""
    pipeline, mic, _factory_calls = _wake_pipeline(monkeypatch)

    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=1.0)

    assert pipeline.start_dictation(target="chat") is True
    dictation = pipeline._dictation_task
    assert dictation is not None
    await asyncio.wait_for(pipeline._wake_handoff_ready.wait(), timeout=1.0)

    await mic.frames.put(RuntimeError("input device went away"))

    await asyncio.wait_for(dictation, timeout=2.0)
    await asyncio.wait_for(wake_task, timeout=1.0)
    assert mic.close_count == 1
    assert pipeline._wake_capture_released.is_set()


# --------------------------------------------------------------------------
# The detectors must not eat the dictated words (AP-24)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_wake_detectors_are_starved_while_a_dictation_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dictation that did NOT take the stream still must not feed wake.

    Reachable whenever the wake microphone opens while a dictation is already
    recording on its own capture. Feeding those frames to a detector both risks
    tripping the wake word on the user's dictated words and runs a second
    consumer on the same native engine.
    """
    pipeline, mic, _factory_calls = _wake_pipeline(monkeypatch)

    seen: list[AudioChunk] = []

    class _CountingWake:
        async def detect(self, chunks: AsyncIterator[AudioChunk]) -> AsyncIterator[str]:
            async for chunk in chunks:
                seen.append(chunk)
            if False:  # pragma: no cover - protocol-shaped async iterator
                yield ""

    pipeline._wake = _CountingWake()  # type: ignore[assignment]

    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=1.0)

    await mic.frames.put(_chunk(b"before"))
    for _ in range(6):
        await asyncio.sleep(0)
    assert len(seen) == 1

    # A dictation is now running on its own capture (the gate is a plain state
    # gate, so the test can set it exactly as ``start_dictation`` does).
    blocking = asyncio.create_task(asyncio.Event().wait())
    pipeline._dictation_task = blocking  # type: ignore[assignment]
    pipeline._dictation_wake_block_until = pipeline_mod.time.time() + 300.0
    try:
        await mic.frames.put(_chunk(b"dictated"))
        for _ in range(6):
            await asyncio.sleep(0)
        assert len(seen) == 1, "dictated audio must never reach a wake detector"
    finally:
        blocking.cancel()
        pipeline._dictation_task = None

    await mic.frames.put(_chunk(b"after"))
    for _ in range(6):
        await asyncio.sleep(0)
    assert len(seen) == 2, "wake hears again the moment the dictation is over"

    wake_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wake_task


@pytest.mark.asyncio
async def test_a_wake_dropped_by_a_dictation_says_so_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The pre-emit gate used to hardcode "desktop activation is unavailable".

    That line is read during live diagnoses, and naming the window when the real
    cause is a running dictation is exactly the wrong-cause detour the shared
    reason resolver exists to prevent.
    """
    pipeline, mic, _factory_calls = _wake_pipeline(monkeypatch)

    release = asyncio.Event()

    class _HeldWake:
        async def detect(self, chunks: AsyncIterator[AudioChunk]) -> AsyncIterator[str]:
            await anext(chunks)
            await release.wait()
            yield "jarvis"

    pipeline._wake = _HeldWake()  # type: ignore[assignment]

    async def _verified(_pcm: bytes) -> bool:
        return True

    pipeline._verify_oww_hit = _verified  # type: ignore[method-assign]

    wake_task = asyncio.create_task(pipeline._run_parallel_wake())
    await asyncio.wait_for(mic.entered.wait(), timeout=1.0)
    await mic.frames.put(_chunk(b"hey"))
    for _ in range(6):
        await asyncio.sleep(0)

    blocking = asyncio.create_task(asyncio.Event().wait())
    pipeline._dictation_task = blocking  # type: ignore[assignment]
    pipeline._dictation_wake_block_until = pipeline_mod.time.time() + 300.0
    try:
        with caplog.at_level("INFO", logger="jarvis.speech.pipeline"):
            release.set()
            await asyncio.wait_for(wake_task, timeout=2.0)
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "Wake detection discarded: a dictation is running" in m for m in messages
        ), messages
    finally:
        blocking.cancel()
        pipeline._dictation_task = None
