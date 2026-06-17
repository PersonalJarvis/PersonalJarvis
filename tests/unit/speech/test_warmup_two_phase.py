"""Two-phase voice warm-up: ready as soon as the listening path is up.

The voice feature used to take ~20 s to become ready *after* the window was
visible because warm-up ran every step sequentially — the dominant cost being
~20 confirmation-audio phrases pre-rendered one at a time. This split warm-up
into:

* **Phase A (critical, parallel):** audio-device stabilization + VAD +
  OpenWakeWord + TTS-client init run concurrently. When it finishes the
  pipeline emits ``VoiceBootStatus(ready=True)`` — the listening path is live.
* **Phase B (background, fire-and-forget):** confirmation audio is pre-rendered
  off the critical path (ACK "Ja?" first, then the task-ack phrases
  concurrently). It must NOT block the ready signal.

These tests pin the contract: ``ready=False`` is emitted before Phase A,
``ready=True`` after Phase A completes, and the background render never blocks
ready. They also keep the BUG-014 audio-stabilization guard and the chime/ready
fallback intact.
"""
from __future__ import annotations

import asyncio

import pytest

import jarvis.speech.pipeline as pl
from jarvis.audio.chime import CHIME_SAMPLE_RATE, READY_PCM
from jarvis.core.events import VoiceBootStatus
from jarvis.speech.pipeline import SpeechPipeline


class FakePlayer:
    def __init__(self) -> None:
        self.set_device_calls: list = []
        self.play_pcm_calls: list = []

    def set_device(self, device) -> None:
        self.set_device_calls.append(device)

    async def play_pcm(self, pcm: bytes, sample_rate: int | None = None) -> None:
        self.play_pcm_calls.append((pcm, sample_rate))


class FakeBus:
    """Records every published event in order for assertions."""

    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, event) -> None:
        self.published.append(event)


class FakeVad:
    def __init__(self) -> None:
        self.ensure_calls = 0

    def _ensure_model(self) -> None:
        self.ensure_calls += 1


class FakeTts:
    """Records init + synthesize; synthesize yields one chunk per call."""

    def __init__(self) -> None:
        self.ensure_calls = 0
        self.synth_phrases: list[str] = []

    def _ensure_client(self) -> None:
        self.ensure_calls += 1

    async def synthesize(self, text, language_code=None):
        self.synth_phrases.append(text)

        class _Chunk:
            pcm = b"\x00\x01"

        yield _Chunk()


class FakeWake:
    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True


def _new_pipe(monkeypatch, *, bus=None, ack_phrase: str = "Ja?") -> SpeechPipeline:
    """Build a warm-up-capable pipeline without running __init__."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._output_device = "auto-headset"
    pipe._player = FakePlayer()
    pipe._bus = bus
    pipe._stt = None
    pipe._vad = FakeVad()
    pipe._tts = FakeTts()
    pipe._wake = FakeWake()
    pipe._openwakeword_enabled = True
    pipe._ack_phrase = ack_phrase
    pipe._ack_pcm = b""
    pipe._task_ack_pcm = {}
    # Stabilization is the BUG-014 guard — stub the underlying probe, not the
    # method, so the device-resolve path keeps running.
    monkeypatch.setattr(
        pl,
        "wait_for_stable_audio_devices",
        lambda **kw: {
            "available": True,
            "device_count": 3,
            "stable": True,
            "waited_s": 0.0,
            "reinits": 0,
            "polls": 1,
        },
    )
    # Keep task-ack pre-render small + deterministic.
    monkeypatch.setattr(
        pl, "iter_all_start_ack", lambda: [("de", "Sofort."), ("en", "Right away.")]
    )
    return pipe


@pytest.mark.asyncio
async def test_emits_ready_false_then_true(monkeypatch) -> None:
    """Boot-status order: ready=False at the very start, ready=True after the
    critical Phase A completes."""
    bus = FakeBus()
    pipe = _new_pipe(monkeypatch, bus=bus)

    await pipe._warmup()

    boot_events = [e for e in bus.published if isinstance(e, VoiceBootStatus)]
    assert len(boot_events) >= 2
    assert boot_events[0].ready is False
    # The last boot-status seen is ready=True.
    assert boot_events[-1].ready is True
    # Order: the first False precedes the first True.
    first_true_idx = next(i for i, e in enumerate(boot_events) if e.ready)
    assert first_true_idx > 0


@pytest.mark.asyncio
async def test_phase_a_runs_critical_loaders(monkeypatch) -> None:
    """Phase A must load VAD, init the TTS client, start OpenWakeWord, and
    re-resolve the output device (BUG-014 guard)."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    await pipe._warmup()

    assert pipe._vad.ensure_calls >= 1
    assert pipe._tts.ensure_calls >= 1
    assert pipe._wake.started is True
    assert pipe._player.set_device_calls == ["auto-headset"]


@pytest.mark.asyncio
async def test_ready_signaled_before_background_render_finishes(monkeypatch) -> None:
    """The ready=True signal must be emitted BEFORE the confirmation-audio
    background render completes — that is the whole point of the split."""
    bus = FakeBus()
    pipe = _new_pipe(monkeypatch, bus=bus)

    # A render barrier the test releases only AFTER inspecting the ready state.
    render_gate = asyncio.Event()
    render_started = asyncio.Event()
    real_synth = pipe._tts.synthesize

    async def gated_synth(text, language_code=None):
        render_started.set()
        await render_gate.wait()
        async for c in real_synth(text, language_code=language_code):
            yield c

    pipe._tts.synthesize = gated_synth  # type: ignore[method-assign]

    await pipe._warmup()

    # ready=True is already on the bus while the background render is blocked.
    boot_events = [e for e in bus.published if isinstance(e, VoiceBootStatus)]
    assert any(e.ready for e in boot_events), "ready=True must be emitted before render finishes"
    assert pipe._ack_pcm == b"", "ACK render must not have completed yet (still gated)"

    # Release the gate and let the background task finish so the test is clean.
    render_gate.set()
    await asyncio.sleep(0)
    bg = pipe._warmup_background_task
    if bg is not None:
        await bg
    assert pipe._ack_pcm != b"", "background render eventually populates the ACK cache"


@pytest.mark.asyncio
async def test_background_renders_ack_and_task_acks(monkeypatch) -> None:
    """Phase B caches the ACK phrase and the task-ack phrases (concurrently)."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    await pipe._warmup()
    bg = pipe._warmup_background_task
    if bg is not None:
        await bg

    assert pipe._ack_pcm != b""
    assert len(pipe._task_ack_pcm) == 2  # both stubbed task-ack phrases cached


@pytest.mark.asyncio
async def test_ready_cue_played_after_phase_a(monkeypatch) -> None:
    """The audible boot-ready cue still plays so the user hears when listening
    starts — the wake/chime fallback for a not-yet-cached ACK is intact."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    await pipe._warmup()

    assert (READY_PCM, CHIME_SAMPLE_RATE) in pipe._player.play_pcm_calls


@pytest.mark.asyncio
async def test_warmup_never_raises_without_bus(monkeypatch) -> None:
    """Headless / no-bus boot: emitting boot status is a guarded no-op."""
    pipe = _new_pipe(monkeypatch, bus=None)

    await pipe._warmup()  # must not raise
    bg = pipe._warmup_background_task
    if bg is not None:
        await bg


@pytest.mark.asyncio
async def test_background_task_cancelled_on_shutdown(monkeypatch) -> None:
    """Phase B is fire-and-forget; on shutdown it must be cancelled + awaited,
    not orphaned. An orphaned task under pythonw.exe has no stderr to surface a
    "Task exception was never retrieved" warning, and a late TTS render could
    write stale PCM into the ACK caches after a live TTS-switch cleared them."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    started = asyncio.Event()

    async def _never_ends() -> None:
        started.set()
        await asyncio.Event().wait()  # blocks until cancelled

    task = asyncio.create_task(_never_ends())
    pipe._warmup_background_task = task
    await started.wait()  # the task is genuinely running

    await pipe._cancel_warmup_background()

    assert task.cancelled(), "the background task must be cancelled, not leaked"
    assert pipe._warmup_background_task is None


@pytest.mark.asyncio
async def test_cancel_background_is_noop_when_absent(monkeypatch) -> None:
    """No background task (headless / already-shut-down): cancel is a safe no-op."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())
    pipe._warmup_background_task = None

    await pipe._cancel_warmup_background()  # must not raise

    assert pipe._warmup_background_task is None
