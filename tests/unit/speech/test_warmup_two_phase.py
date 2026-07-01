"""Two-phase voice warm-up: ready as soon as the listening path is up.

The voice feature used to take ~20 s to become ready *after* the window was
visible because warm-up ran every step sequentially — the dominant cost being
~20 confirmation-audio phrases pre-rendered one at a time. This split warm-up
into:

* **Phase A (critical):** audio-device stabilization + wake-loop start. It does
  the absolute minimum so the wake LOOP is listening fast; the heavy VAD/STT/TTS
  loads are deferred so they never gate wake-loop start (BUG class: "Hey Jarvis"
  dead while heavy loads serialize on the import lock).
* **Deferred loaders (background):** wake model + VAD + TTS-client load off the
  wake-critical path. HONEST readiness lives here: ``VoiceBootStatus(ready=True)``
  and the audible "you can speak" cue fire only at the END of this task — the
  first moment wake (model) + VAD + TTS are ALL up. Flipping ready in Phase A
  (before TTS) was the "it says ready but I can't talk" bug (2026-06-29).
* **Phase B (background, fire-and-forget):** confirmation audio is pre-rendered
  off the critical path (ACK "Ja?" first, then the task-ack phrases
  concurrently). It must NOT block the ready signal.

These tests pin the contract: ``ready=False`` is emitted before Phase A,
``ready=True`` (and the cue) only after the DEFERRED loaders complete the full
stack, and the Phase-B confirmation render never blocks ready. They also keep
the BUG-014 audio-stabilization guard and the chime/ready fallback intact.
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
    # Honest-ready anchor (2026-06-27): _warmup reads _whisper_wake_enabled to
    # decide whether wake is hear-ready at Phase A (openWakeWord) or only after
    # the wake model loads (custom rolling-whisper). With _stt=None this is the
    # openWakeWord path, exactly as the real __init__ computes it.
    pipe._whisper_wake_enabled = False
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
    """Boot-status order: ready=False at the very start, ready=True only after
    the DEFERRED loaders bring up the full stack (honest readiness)."""
    bus = FakeBus()
    pipe = _new_pipe(monkeypatch, bus=bus)

    await pipe._warmup()
    # Honest readiness fires from the background deferred-loaders task, not from
    # _warmup itself (which returns fast so the wake loop starts immediately).
    await pipe._deferred_warmup_task

    boot_events = [e for e in bus.published if isinstance(e, VoiceBootStatus)]
    assert len(boot_events) >= 2
    assert boot_events[0].ready is False
    # The last boot-status seen is ready=True.
    assert boot_events[-1].ready is True
    # Order: the first False precedes the first True.
    first_true_idx = next(i for i, e in enumerate(boot_events) if e.ready)
    assert first_true_idx > 0


@pytest.mark.asyncio
async def test_phase_a_runs_wake_critical_loaders(monkeypatch) -> None:
    """Phase A must do exactly the wake-critical work: start OpenWakeWord and
    re-resolve the output device (BUG-014 guard). The heavy VAD/TTS loads are
    NOT in Phase A anymore — they are deferred — so they must NOT have run by
    the time the wake path is ready."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    await pipe._warmup()

    assert pipe._wake.started is True
    assert pipe._player.set_device_calls == ["auto-headset"]


@pytest.mark.asyncio
async def test_deferred_loaders_eventually_load_vad_and_tts(monkeypatch) -> None:
    """VAD + TTS are not skipped — they load via the background deferred task so
    they are ready by the first post-wake turn, just off the wake-ready path."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    await pipe._warmup()
    dbg = pipe._deferred_warmup_task
    assert dbg is not None, "deferred VAD/STT/TTS load must run as a background task"
    await dbg

    assert pipe._vad.ensure_calls >= 1
    assert pipe._tts.ensure_calls >= 1


@pytest.mark.asyncio
async def test_phase_a_does_not_block_on_heavy_loads(monkeypatch) -> None:
    """The wake-critical path (audio-stabilize + OpenWakeWord start) must reach
    ready WITHOUT waiting on the heavy VAD/STT/TTS model loads.

    Forensic (2026-06-22): those loads each lazy-import a big C-extension
    (onnxruntime for the wake model + Silero, ctranslate2 for Whisper). Run
    concurrently inside the boot storm they serialize on the Python import lock
    and get starved to 7-24 s (measured: ``wake-start=14187, vad-load=12672``).
    Because the wake loop was started only AFTER the whole warm-up finished,
    "Hey Jarvis" was dead for that entire window. VAD/STT/TTS are only needed
    AFTER a wake (and load lazily on first use), so they must move off the
    ready path into a background task and never gate wake readiness.
    """
    import threading

    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    vad_release = threading.Event()

    class _BlockingVad:
        def __init__(self) -> None:
            self.ensure_calls = 0

        def _ensure_model(self) -> None:
            # Synchronous load (runs in a worker thread via asyncio.to_thread);
            # block until the test releases it, modelling a starved model load.
            vad_release.wait(5.0)
            self.ensure_calls += 1

    pipe._vad = _BlockingVad()

    # Phase A must COMPLETE (wake model started) even though the VAD load is
    # still blocked. Without the decoupling this awaits the blocked load and the
    # wait_for trips → the regression is reproduced.
    await asyncio.wait_for(pipe._warmup_phase_a(), timeout=3.0)

    assert pipe._wake.started is True, "wake model must be started by Phase A"
    assert pipe._vad.ensure_calls == 0, "the heavy VAD load must NOT block Phase A"

    # The load is not skipped — it runs as a background (deferred) task that the
    # release lets finish.
    dbg = getattr(pipe, "_deferred_warmup_task", None)
    assert dbg is not None, "VAD/STT/TTS must load via a background deferred task"
    vad_release.set()
    await asyncio.wait_for(dbg, timeout=3.0)
    assert pipe._vad.ensure_calls >= 1, "the deferred VAD load must eventually run"


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
    # Honest readiness comes from the deferred loaders (which use the TTS
    # client's _ensure_client, NOT synthesize), so it completes while the Phase-B
    # confirmation render (synthesize) is still gated.
    await pipe._deferred_warmup_task

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
async def test_ready_cue_played_after_deferred_loaders(monkeypatch) -> None:
    """The audible boot-ready cue still plays so the user hears when the voice
    stack is genuinely ready — now fired at the END of the deferred loaders
    (honest readiness), not after Phase A."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    await pipe._warmup()
    await pipe._deferred_warmup_task
    task = pipe._warmup_ready_cue_task
    assert task is not None
    await task

    assert (READY_PCM, CHIME_SAMPLE_RATE) in pipe._player.play_pcm_calls


@pytest.mark.asyncio
async def test_ready_cue_does_not_block_wake_loop_start(monkeypatch) -> None:
    """The ready chime must not sit between ready=True and the wake loop.

    Live logs showed Phase A completing quickly, then ``_play_ready_cue`` holding
    ``_warmup()`` for several seconds before ``Pipeline ready`` and the wake
    listener started. A wedged/slow output device must not keep the wake word
    dead after the critical listening path is ready.
    """
    bus = FakeBus()
    pipe = _new_pipe(monkeypatch, bus=bus)
    cue_started = asyncio.Event()
    cue_release = asyncio.Event()

    async def _blocking_ready_cue() -> None:
        cue_started.set()
        await cue_release.wait()

    pipe._play_ready_cue = _blocking_ready_cue  # type: ignore[method-assign]

    await asyncio.wait_for(pipe._warmup(), timeout=0.5)
    # The cue is created + fired by the deferred-loaders task (honest readiness)
    # as a fire-and-forget task, so neither _warmup NOR the deferred task itself
    # is blocked by a slow/wedged output device.
    await asyncio.wait_for(pipe._deferred_warmup_task, timeout=0.5)

    boot_events = [e for e in bus.published if isinstance(e, VoiceBootStatus)]
    assert any(e.ready for e in boot_events), "ready=True must be emitted"
    await asyncio.wait_for(cue_started.wait(), timeout=0.5)

    task = pipe._warmup_ready_cue_task
    assert task is not None and not task.done()
    cue_release.set()
    await asyncio.wait_for(task, timeout=0.5)


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


@pytest.mark.asyncio
async def test_deferred_warmup_task_cancelled_on_shutdown(monkeypatch) -> None:
    """The deferred wake/VAD/TTS loader task is fire-and-forget; on shutdown it
    must be cancelled + awaited, not orphaned. It owns the honest ready signal
    and spawns the ready-cue task, so a leak here strands both."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    started = asyncio.Event()

    async def _never_ends() -> None:
        started.set()
        await asyncio.Event().wait()  # blocks until cancelled

    task = asyncio.create_task(_never_ends())
    pipe._deferred_warmup_task = task
    await started.wait()  # the task is genuinely running

    await pipe._cancel_warmup_background()

    assert task.cancelled(), "the deferred loader task must be cancelled, not leaked"
    assert pipe._deferred_warmup_task is None


@pytest.mark.asyncio
async def test_ready_cue_task_cancelled_on_shutdown(monkeypatch) -> None:
    """The boot-ready audio cue task is created INSIDE the deferred loaders; a
    shutdown after it is armed must cancel + await it, not orphan it (a wedged
    output device must not keep the cue alive past pipeline shutdown). The
    cue-task-may-be-None-before-creation race is covered by the getattr() in
    _cancel_warmup_background and the noop test above."""
    pipe = _new_pipe(monkeypatch, bus=FakeBus())

    started = asyncio.Event()

    async def _never_ends() -> None:
        started.set()
        await asyncio.Event().wait()  # blocks until cancelled

    task = asyncio.create_task(_never_ends())
    pipe._warmup_ready_cue_task = task
    await started.wait()  # the task is genuinely running

    await pipe._cancel_warmup_background()

    assert task.cancelled(), "the ready-cue task must be cancelled, not leaked"
    assert pipe._warmup_ready_cue_task is None
