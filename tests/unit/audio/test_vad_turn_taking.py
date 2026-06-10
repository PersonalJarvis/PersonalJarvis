from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np
import pytest

from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.core.protocols import AudioChunk


def _pcm_frame(amplitude: float) -> bytes:
    samples = np.full(VAD_FRAME_SAMPLES, amplitude, dtype=np.float32)
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


async def _chunks(frames: list[bytes]) -> AsyncIterator[AudioChunk]:
    for index, pcm in enumerate(frames):
        yield AudioChunk(
            pcm=pcm,
            sample_rate=16_000,
            timestamp_ns=index,
            channels=1,
        )


async def _collect(vad: SileroEndpointer, frames: list[bytes]) -> list[bytes]:
    out: list[bytes] = []
    async for utterance in vad.utterances(_chunks(frames)):
        out.append(utterance)
    return out


def _stub_vad(vad: SileroEndpointer, probs: list[float]) -> None:
    vad._ensure_model = lambda: None  # type: ignore[method-assign]
    iterator = iter(probs)
    vad._prob = lambda _frame: next(iterator, 0.0)  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_short_pause_does_not_end_turn() -> None:
    vad = SileroEndpointer(silence_ms=320, min_speech_ms=96)
    probs = [0.9] * 5 + [0.0] * 5 + [0.9] * 5 + [0.0] * 10
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.05) for _ in probs]
    utterances = await _collect(vad, frames)

    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_complete_silence_with_high_vad_probability_is_ignored() -> None:
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96, min_speech_rms=0.002)
    probs = [0.95] * 20
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.0) for _ in probs]
    utterances = await _collect(vad, frames)

    assert utterances == []


@pytest.mark.asyncio
async def test_single_frame_false_start_is_discarded() -> None:
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=160)
    probs = [0.9] + [0.0] * 4
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.05) for _ in probs]
    utterances = await _collect(vad, frames)

    assert utterances == []


@pytest.mark.asyncio
async def test_energy_drop_ends_turn_even_when_vad_probability_stays_high() -> None:
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96, min_speech_rms=0.002)
    probs = [0.9] * 10
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.08) for _ in range(4)] + [_pcm_frame(0.004) for _ in range(6)]
    utterances = await _collect(vad, frames)

    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_external_endpoint_request_ends_turn_during_continuous_speech() -> None:
    """Speaker bleed (music, podcast) keeps Silero on 'speech' indefinitely
    so the silence endpoint never fires. An external stability probe must
    be able to force the endpoint via the probe hook once enough speech
    has been collected."""
    endpoint_reasons: list[str] = []
    probe_calls: list[bytes] = []

    # Build the VAD without probe_callback, then attach one after init so
    # we can close over the VAD instance (to call request_endpoint()).
    vad = SileroEndpointer(
        silence_ms=10_000,  # absurd high — silence endpoint would never fire
        min_speech_ms=96,
        min_speech_rms=0.002,
        on_endpoint=lambda reason: endpoint_reasons.append(reason),
        probe_interval_ms=64,
        probe_min_active_ms=320,
    )

    def probe_request_endpoint(pcm: bytes, _loud: bool) -> None:
        probe_calls.append(pcm)
        # First probe → request the endpoint.
        if len(probe_calls) == 1:
            vad.request_endpoint()

    vad._probe_callback = probe_request_endpoint  # type: ignore[assignment]

    probs = [0.9] * 30
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.08) for _ in probs]
    utterances = await _collect(vad, frames)

    assert len(utterances) == 1
    assert "stt_stable" in endpoint_reasons
    assert len(probe_calls) >= 1


@pytest.mark.asyncio
async def test_probe_callback_fires_only_after_min_active_duration() -> None:
    """The probe must not fire before probe_min_active_ms of speech, so
    short commands don't generate wasted STT calls."""
    probe_calls: list[bytes] = []
    vad = SileroEndpointer(
        silence_ms=5_000,
        min_speech_ms=64,
        min_speech_rms=0.002,
        probe_callback=lambda pcm, _loud: probe_calls.append(pcm),
        probe_interval_ms=64,       # probe-eligible every 2 frames
        probe_min_active_ms=320,    # only after 10 frames of speech
    )
    probs = [0.9] * 20  # 20 frames of continuous speech
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.08) for _ in probs]

    async def run_with_early_request() -> list[bytes]:
        out: list[bytes] = []
        async for utterance in vad.utterances(_chunks(frames)):
            out.append(utterance)
        return out

    # Force an endpoint after a few frames so the test doesn't hang.
    vad.request_endpoint()
    await run_with_early_request()

    # At least one probe must have fired (after frame 10), but not from frame 0.
    assert len(probe_calls) >= 1


@pytest.mark.asyncio
async def test_brief_speaker_bleed_spikes_do_not_reset_silence_timer() -> None:
    """Regression for the 2026-05-25 "Jarvis denkt, ich rede noch" bug.

    In a noisy room (the user listens to music while working) Silero holds
    prob~=1.0 even on near-silence, and short ambient/bleed spikes (a drum
    hit, a fan gust, a TV transient) briefly raise a single frame's RMS
    above the relative-silence floor. The old state machine reset
    ``silent_run`` to 0 on *every* such single speech frame, so the silence
    endpoint never accumulated to its threshold and the turn only ended at
    the ``max_utterance`` hard cap (8 s in production). The user had long
    finished talking, but Jarvis kept "listening" for several more seconds
    and the captured buffer was mostly noise -> often an unusable final
    transcript ("he thinks I'm still talking and never answers").

    Empirical evidence (data/jarvis_desktop.log, 14:46:37-45)::

        silence timer start  rms=0.0053 prob=1.000   <- user stopped
        silence timer cancel rms=0.0225 prob=0.999   <- single spike resets
        silence timer start  rms=0.0018 prob=0.997
        silence timer cancel rms=0.0180 prob=1.000
        ... (dozens of times) ...
        voice activity stop: reason=max_utterance duration_ms=8000 speech_ms=2528

    A brief speech blip must NOT cancel an in-progress silence timer; only
    *sustained* speech (``cancel_hysteresis_ms``) counts as the user
    resuming. The frame layout below keeps every uninterrupted silence run
    below the endpoint threshold, so the OLD logic never fires (each spike
    resets the run); only the hysteresis fix lets ``silent_run`` survive the
    isolated spikes and reach the threshold.
    """
    vad = SileroEndpointer(
        silence_ms=320,            # 10 silence frames to endpoint
        min_speech_ms=96,
        min_speech_rms=0.002,
        cancel_hysteresis_ms=96,   # 3 sustained speech frames needed to cancel
    )

    probs: list[float] = []
    frames: list[bytes] = []

    def add(prob: float, amp: float, count: int) -> None:
        for _ in range(count):
            probs.append(prob)
            frames.append(_pcm_frame(amp))

    # Real speech (peak rms 0.06), then two near-silence runs of 8 frames
    # each (< the 10-frame threshold) separated by a single-frame bleed spike
    # that Silero still scores at prob=1.0. The troughs sit just below the
    # speech floor (rms 0.0015 < min_speech_rms 0.002) so they are plain
    # silence and never start a spurious follow-up utterance.
    add(1.0, 0.06, 5)         # real speech (peak rms 0.06)
    add(1.0, 0.0015, 8)       # near-silence run 1 (held mic)
    add(1.0, 0.05, 1)         # isolated bleed spike
    add(1.0, 0.0015, 8)       # near-silence run 2 -> crosses the threshold

    _stub_vad(vad, probs)
    utterances = await _collect(vad, frames)

    # OLD logic: each uninterrupted silence run is 8 < 10 and the spike resets
    # silent_run -> the silence endpoint never fires -> 0 utterances.
    # FIX: the single-frame spike is absorbed, silent_run survives it and
    # reaches 10 across the two runs -> exactly 1 utterance.
    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_sustained_speech_still_cancels_silence_timer() -> None:
    """The hysteresis must not swallow a genuine resume. When the user pauses
    and then keeps talking for longer than ``cancel_hysteresis_ms``, the
    silence timer must cancel so the turn does not end mid-sentence
    (BUG-018 guard: never cut the user off)."""
    vad = SileroEndpointer(
        silence_ms=320,            # 10 silence frames to endpoint
        min_speech_ms=96,
        min_speech_rms=0.002,
        cancel_hysteresis_ms=96,   # 3 sustained speech frames to cancel
    )

    probs: list[float] = []
    frames: list[bytes] = []

    def add(prob: float, amp: float, count: int) -> None:
        for _ in range(count):
            probs.append(prob)
            frames.append(_pcm_frame(amp))

    add(1.0, 0.06, 5)        # real speech
    add(0.0, 0.003, 8)       # breathing pause (< 10 frames, no endpoint)
    add(1.0, 0.06, 12)       # user resumes — sustained, must cancel the timer
    add(0.0, 0.003, 10)      # real end-of-turn silence -> endpoint

    _stub_vad(vad, probs)
    utterances = await _collect(vad, frames)

    # Exactly one utterance, and it must include the resumed speech (the
    # pause did not prematurely end the turn).
    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_probe_reports_tail_loud_for_bleed_and_quiet_for_pause() -> None:
    """The probe callback must report whether the tail carries bleed-level
    energy, so the pipeline can tell a quiet thinking-pause from loud speaker
    bleed. ``tail_loud`` uses the same relative-silence calibration as the
    per-frame silence gate: a tail at or below ``peak * ratio`` is a genuine
    pause (quiet), anything above is speaker bleed (loud)."""
    loud_flags: list[bool] = []
    vad = SileroEndpointer(
        silence_ms=10_000,          # absurd high → silence endpoint never fires
        min_speech_ms=64,
        min_speech_rms=0.002,
        probe_callback=lambda _pcm, loud: loud_flags.append(loud),
        probe_interval_ms=32,       # probe-eligible every frame
        probe_min_active_ms=160,    # after 5 frames of speech
        probe_tail_ms=160,          # tail = last 5 frames
    )
    probs = [0.9] * 30
    _stub_vad(vad, probs)

    # 8 loud speech frames (peak rms 0.08) then 22 quiet held-mic frames
    # (rms 0.004, below peak*0.22=0.0176). Early probes see a loud tail; once
    # the 5-frame tail window slides fully into the quiet region the probe
    # reports tail_loud=False.
    frames = [_pcm_frame(0.08) for _ in range(8)] + [_pcm_frame(0.004) for _ in range(22)]
    await _collect(vad, frames)

    assert True in loud_flags, "bleed-level (loud) tail was never reported while speaking"
    assert False in loud_flags, "quiet tail during the pause was never reported"


@pytest.mark.asyncio
async def test_forced_cut_then_pure_silence_flushes_tail_endpoint() -> None:
    """Regression for the 2026-06-09 "Jarvis listens forever" hang.

    When the max-utterance cap force-cuts an utterance, the pipeline buffers
    the fragment (``_carry_pcm``) and relies on the VAD to deliver ANOTHER
    endpoint to finalize the merged turn. But a silence endpoint only exists
    inside an active speech phase — if the user finished their sentence right
    inside the capped window and stays silent, no speech phase ever starts
    again, no endpoint ever fires, and the buffered sentence is never
    submitted (LISTENING forever; log evidence 2026-06-09 22:24:13).

    Fix contract: after a ``max_utterance`` cut, ``silence_ms`` of post-cut
    silence must yield an (empty) tail with reason ``silence`` so the
    consumer finalizes its carry.
    """
    endpoint_reasons: list[str] = []
    vad = SileroEndpointer(
        silence_ms=320,        # 10 silence frames to endpoint
        min_speech_ms=96,
        max_utterance_s=1,     # cap at 16_000 samples
        on_endpoint=lambda reason: endpoint_reasons.append(reason),
    )
    # 31 speech frames hit the cap exactly (the start frame is counted twice
    # via the pre-buffer, so total_frames reaches 32 = the 16_000-sample cap
    # on the 31st frame), then pure silence — the user finished right inside
    # the capped window.
    probs = [0.9] * 31 + [0.0] * 15
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.05) for _ in range(31)] + [
        _pcm_frame(0.0) for _ in range(15)
    ]

    utterances = await _collect(vad, frames)

    assert endpoint_reasons[0] == "max_utterance"
    assert "silence" in endpoint_reasons, (
        "post-cut silence must fire a tail-flush endpoint so the pipeline "
        "finalizes its forced-cut carry"
    )
    assert len(utterances) == 2
    assert utterances[1] == b""


@pytest.mark.asyncio
async def test_forced_cut_then_false_start_blip_still_flushes_tail() -> None:
    """Second hole of the same class: after a forced cut, a short noise blip
    (< min_speech_ms) is discarded as ``false_start`` WITHOUT yielding —
    the carry must still be flushed by the next silence run instead of
    hanging forever."""
    endpoint_reasons: list[str] = []
    vad = SileroEndpointer(
        silence_ms=320,        # 10 silence frames
        min_speech_ms=96,      # 3 speech frames required
        max_utterance_s=1,
        on_endpoint=lambda reason: endpoint_reasons.append(reason),
    )
    # 31 speech frames → cut; 5 silence; 1-frame blip (the pre-buffer start
    # double-count makes it 2 speech frames < the 3-frame minimum → false
    # start); then silence.
    probs = [0.9] * 31 + [0.0] * 5 + [0.9] * 1 + [0.0] * 25
    _stub_vad(vad, probs)
    frames = (
        [_pcm_frame(0.05) for _ in range(31)]
        + [_pcm_frame(0.0) for _ in range(5)]
        + [_pcm_frame(0.05) for _ in range(1)]
        + [_pcm_frame(0.0) for _ in range(25)]
    )

    utterances = await _collect(vad, frames)

    assert endpoint_reasons[0] == "max_utterance"
    assert "false_start" in endpoint_reasons
    assert endpoint_reasons[-1] == "silence"
    assert len(utterances) == 2
    assert utterances[1] == b""


@pytest.mark.asyncio
async def test_forced_cut_then_user_resumes_no_extra_tail_flush() -> None:
    """When the user keeps talking after the cut (the normal case), the
    follow-up segment ends with a regular silence endpoint that already
    finalizes the carry — no additional empty tail flush may follow."""
    endpoint_reasons: list[str] = []
    vad = SileroEndpointer(
        silence_ms=320,
        min_speech_ms=96,
        max_utterance_s=1,
        on_endpoint=lambda reason: endpoint_reasons.append(reason),
    )
    # 31 speech frames → cut; user keeps talking 10 frames; 25 silence frames
    # (10 fire the natural endpoint, the surplus 15 must NOT flush again).
    probs = [0.9] * 31 + [0.9] * 10 + [0.0] * 25
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.05) for _ in range(41)] + [
        _pcm_frame(0.0) for _ in range(25)
    ]

    utterances = await _collect(vad, frames)

    assert endpoint_reasons == ["max_utterance", "silence"]
    assert len(utterances) == 2
    assert utterances[1] != b""


@pytest.mark.asyncio
async def test_probe_payload_is_tail_only_not_full_buffer() -> None:
    """The probe must receive only the last `probe_tail_ms` of audio,
    not the entire growing utterance buffer. This is critical: probing
    the full buffer would feed more and more music into Whisper on each
    call, producing fluctuating hallucinations and never stabilising."""
    probe_calls: list[bytes] = []
    # 16 kHz int16 PCM → 2 bytes per sample. tail_ms=320 → 5_120 samples
    # → 10_240 bytes payload.
    expected_tail_bytes = (320 // 32) * VAD_FRAME_SAMPLES * 2

    vad = SileroEndpointer(
        silence_ms=5_000,
        min_speech_ms=64,
        min_speech_rms=0.002,
        probe_callback=lambda pcm, _loud: probe_calls.append(pcm),
        probe_interval_ms=64,
        probe_min_active_ms=320,
        probe_tail_ms=320,  # tiny tail for the test
    )
    # 60 frames total — way more than the tail size.
    probs = [0.9] * 60
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.08) for _ in probs]

    vad.request_endpoint()
    await _collect(vad, frames)

    assert probe_calls, "expected at least one probe call"
    for payload in probe_calls:
        assert len(payload) == expected_tail_bytes, (
            f"probe payload should be tail-only "
            f"({expected_tail_bytes} bytes), got {len(payload)}"
        )
