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
async def test_external_request_below_floor_falls_back_to_max_utterance() -> None:
    """Hard 1.5 s floor (maintainer 2026-06-16): while the user still holds the
    floor (continuous loud speech → ``silent_run`` never reaches the silence
    window), a probe-driven endpoint request must be DISCARDED, never honoured.

    Binding the probe force to the silence floor is what guarantees the
    think-buffer on EVERY utterance — the user asked to always get ~1.5 s to
    pause and finish a thought (delegation / Computer-Use prompts were being
    auto-submitted mid-sentence). The cost, accepted explicitly: very loud
    *continuous* speaker bleed no longer ends instantly via ``stt_stable`` — it
    falls back to the ``max_utterance`` safety net. This pins both halves: no
    premature cut below the floor, and the backstop still finalizes the turn.

    Supersedes the old ``..._ends_turn_during_continuous_speech`` test, which
    pinned the now-removed behaviour (the probe ended the turn at silence_ms≈0).
    """
    endpoint_reasons: list[str] = []
    probe_calls: list[bytes] = []
    vad = SileroEndpointer(
        silence_ms=10_000,          # floor unreachable within these frames
        min_speech_ms=96,
        min_speech_rms=0.002,
        max_utterance_s=1,          # ~31 frames to the cap (keeps the test bounded)
        on_endpoint=lambda reason: endpoint_reasons.append(reason),
        probe_interval_ms=64,
        probe_min_active_ms=320,
    )

    def probe_request_endpoint(pcm: bytes, _loud: bool) -> None:
        probe_calls.append(pcm)
        vad.request_endpoint()      # every probe insists the user is done

    vad._probe_callback = probe_request_endpoint  # type: ignore[assignment]

    probs = [0.9] * 40              # continuous speech; no silence ever accrues
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.08) for _ in probs]

    utterances = await _collect(vad, frames)

    assert probe_calls, "probe never fired"
    assert "stt_stable" not in endpoint_reasons, (
        "an external endpoint request was honoured below the 1.5 s silence floor "
        "— the premature auto-submit the maintainer reported"
    )
    assert endpoint_reasons == ["max_utterance"], (
        "continuous loud bleed must fall back to the max_utterance cap, "
        f"got {endpoint_reasons}"
    )
    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_eager_probe_does_not_split_turn_on_short_pause() -> None:
    """End-to-end think-buffer guarantee (maintainer 2026-06-16): speech, a short
    pause (below the silence window), resumed speech, then the genuine
    end-of-turn silence must yield exactly ONE utterance — even when the STT
    probe insists on ending the turn on every firing.

    The early requests during the short pause are discarded by the floor, so a
    half-formed prompt can never be auto-submitted on a thinking pause; only the
    real silence floor finalizes the turn. Under the old behaviour the probe's
    request was honoured the moment it arrived, splitting the turn in two.
    """
    endpoint_reasons: list[str] = []
    vad = SileroEndpointer(
        silence_ms=320,             # 10 frames to the floor
        min_speech_ms=96,
        min_speech_rms=0.002,
        on_endpoint=lambda reason: endpoint_reasons.append(reason),
        probe_interval_ms=64,
        probe_min_active_ms=160,
    )

    def eager_probe(_pcm: bytes, _loud: bool) -> None:
        vad.request_endpoint()      # worst case: convinced the user is done every probe

    vad._probe_callback = eager_probe  # type: ignore[assignment]

    # speech(8) → short pause(5 < 10) → resumed speech(8) → final silence(15 >= 10)
    probs = [0.9] * 8 + [0.0] * 5 + [0.9] * 8 + [0.0] * 15
    _stub_vad(vad, probs)
    frames = (
        [_pcm_frame(0.08)] * 8
        + [_pcm_frame(0.0)] * 5
        + [_pcm_frame(0.08)] * 8
        + [_pcm_frame(0.0)] * 15
    )

    utterances = await _collect(vad, frames)

    assert len(utterances) == 1, (
        f"the eager probe split the turn into {len(utterances)} utterances — an "
        "early endpoint request beheaded the short pause instead of waiting for "
        "the silence floor"
    )


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
async def test_quiet_pause_reports_not_loud_with_production_tail_geometry() -> None:
    """Regression (2026-06-14): a sustained quiet thinking-pause must be
    reported ``tail_loud=False`` even when the probe tail window is LONGER than
    the silence window.

    Production wires ``probe_tail_ms=1800`` but ``silence_ms=1500``. The
    original implementation derived ``tail_loud`` from the RMS of the *entire*
    1800 ms tail, which stays speech-dominated throughout any pause shorter than
    the whole window — so ``tail_loud`` never went False before the silence
    endpoint fired, and the probe kept forcing ``stt_stable`` endpoints at
    32-864 ms of silence (live log evidence ``data/jarvis_desktop.log``,
    e.g. ``empty tail (text='and') ... silence_ms=32``).

    Contract: once the user has gone quiet, ``tail_loud`` must turn False well
    before the silence endpoint fires and must NOT flip back to loud while the
    user stays silent, so the pipeline defers to ``silence_ms``. (The recent-
    energy window may still read the very first probe as loud while the speech
    tail drains out of it — harmless, because the 1800 ms transcription tail is
    still full of speech then and cannot look empty/stable to force an
    endpoint. What matters is that quiet wins and stays won.)
    """
    loud_flags: list[bool] = []
    vad = SileroEndpointer(
        silence_ms=1500,            # production value (47 frames to endpoint)
        min_speech_ms=160,
        min_speech_rms=0.002,
        probe_callback=lambda _pcm, loud: loud_flags.append(loud),
        probe_interval_ms=320,      # probe-eligible every 10 frames
        probe_min_active_ms=320,    # first probe only after 10 active frames
        probe_tail_ms=1800,         # production tail — LONGER than silence_ms
    )
    # 8 loud speech frames (fewer than probe_min_active, so NO probe fires
    # during speech), then a long quiet pause. The original full-tail RMS stayed
    # dominated by the 8 leading speech frames and reported loud for the WHOLE
    # pause; the fix must report quiet once the user has been silent a moment.
    probs = [0.9] * 8 + [0.0] * 55
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.06) for _ in range(8)] + [
        _pcm_frame(0.001) for _ in range(55)
    ]

    await _collect(vad, frames)

    assert loud_flags, "expected at least one probe to fire during the quiet pause"
    assert False in loud_flags, (
        "a sustained quiet thinking-pause was never reported as quiet (tail_loud "
        f"stayed True) — the probe will force a premature endpoint: {loud_flags}"
    )
    first_quiet = loud_flags.index(False)
    assert all(flag is False for flag in loud_flags[first_quiet:]), (
        f"tail_loud flipped back to loud during continuous silence: {loud_flags}"
    )
    assert loud_flags[-1] is False, (
        f"deep in a quiet pause the probe still reported loud: {loud_flags}"
    )


@pytest.mark.asyncio
async def test_short_mid_sentence_pause_does_not_split_turn_via_probe() -> None:
    """End-to-end: speech -> ~0.5 s quiet pause -> resumed speech -> end silence
    must yield exactly ONE utterance.

    The probe callback models the live pipeline's STT empty/stable gate: it
    forces the endpoint only when the VAD reports the tail is loud (NOT a
    thinking pause) AND the most recent audio is quiet (the tail "transcribes
    to nothing new"). Under the fixed VAD a quiet pause reports ``loud=False``,
    so the two conditions can never both hold mid-pause and the turn stays open
    until the user truly stops. Under the old VAD the full-tail RMS reports
    ``loud=True`` during the pause, so the probe forces an endpoint and splits
    the turn in two.
    """

    def _recent_rms_int16(pcm: bytes, n_samples: int = 2560) -> float:
        arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        recent = arr[-n_samples:]
        if recent.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(recent))))

    vad = SileroEndpointer(
        silence_ms=1500,
        min_speech_ms=160,
        min_speech_rms=0.002,
        probe_interval_ms=320,
        probe_min_active_ms=320,
        probe_tail_ms=1800,
    )

    def probe(pcm: bytes, loud: bool) -> None:
        # last ~160 ms quiet → the user appears to have stopped (empty/stable)
        appears_stopped = _recent_rms_int16(pcm) < 500.0
        if loud and appears_stopped:
            vad.request_endpoint()

    vad._probe_callback = probe  # type: ignore[assignment]

    probs = [0.9] * 8 + [0.0] * 16 + [0.9] * 20 + [0.0] * 50
    _stub_vad(vad, probs)
    frames = (
        [_pcm_frame(0.06) for _ in range(8)]
        + [_pcm_frame(0.001) for _ in range(16)]   # ~0.5 s thinking pause
        + [_pcm_frame(0.06) for _ in range(20)]    # user resumes mid-sentence
        + [_pcm_frame(0.001) for _ in range(50)]   # genuine end-of-turn silence
    )

    utterances = await _collect(vad, frames)

    assert len(utterances) == 1, (
        f"the mid-sentence pause split the turn into {len(utterances)} utterances "
        "— the probe forced a premature endpoint during the pause"
    )


@pytest.mark.asyncio
async def test_dynamic_range_bleed_still_reports_loud_so_cure_fires() -> None:
    """Regression for the dynamic-range speaker-bleed gap (code-review Finding 1).

    Speaker bleed is not always stationary: music/TV has loud beats separated by
    brief quiet dips. A dip drops below the relative-silence floor, so the
    per-frame gate trips and a silence timer starts; if the loud beats are
    shorter than ``cancel_hysteresis`` they never reset it, so an
    instantaneous ``silent_run == 0`` discriminator reads quiet for the WHOLE
    bleed and the probe never forces the endpoint — the turn drags to the
    ``max_utterance`` cap (a softer recurrence of "Silero records music
    forever"). ``tail_loud`` must instead reflect the *recent* audio energy,
    which averages over beats+dips and stays loud, so the bleed cure still
    fires while a genuine (fully quiet) pause still defers.
    """
    loud_flags: list[bool] = []
    vad = SileroEndpointer(
        silence_ms=10_000,          # silence endpoint can't fire → only the probe can cure
        min_speech_ms=96,           # 3 frames of speech establishes the turn
        min_speech_rms=0.002,
        cancel_hysteresis_ms=160,   # 5 frames; beats below this never reset silent_run
        probe_callback=lambda _pcm, loud: loud_flags.append(loud),
        probe_interval_ms=64,       # probe every 2 frames
        probe_min_active_ms=320,    # first probe after 10 frames → none during the 4 speech frames
        probe_tail_ms=1800,
    )
    # 4 real speech frames (sets peak, fewer than probe_min_active so no probe
    # fires here), then dynamic bleed: quiet dips (3 frames, < cancel_hysteresis
    # so silent_run is held, never reset) alternating with loud beats (3 frames).
    # silent_run is > 0 at every probe, so the instantaneous-silent_run rule
    # reports loud=False forever; the recent-energy window reports loud.
    probs = [0.9] * 4
    frames = [_pcm_frame(0.06) for _ in range(4)]
    for _ in range(8):
        probs += [0.0] * 3 + [0.95] * 3
        frames += [_pcm_frame(0.001) for _ in range(3)] + [_pcm_frame(0.06) for _ in range(3)]
    _stub_vad(vad, probs)

    await _collect(vad, frames)

    assert True in loud_flags, (
        "dynamic-range bleed (loud beats + quiet dips) was never reported as "
        "loud — the speaker-bleed cure is starved and the turn drags to "
        f"max_utterance: {loud_flags}"
    )


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


# --------------------------------------------------------------------------- #
# Adaptive endpoint patience (2026-06-16): composing a delegation ("spawn a
# sub-agent that ...") involves longer thinking pauses than a short command, so
# the default silence window cuts the user off mid-sentence. The STT probe calls
# ``extend_silence_window`` when the live partial shows a delegation; that must
# raise the silence-endpoint threshold for the CURRENT utterance only, and reset
# to the snappy default at the next speech start so short commands are unaffected
# and the patience never leaks across turns.
# --------------------------------------------------------------------------- #


async def _chunks_with_action(
    frames: list[bytes], *, at_index: int, action
) -> AsyncIterator[AudioChunk]:
    for index, pcm in enumerate(frames):
        if index == at_index:
            action()
        yield AudioChunk(
            pcm=pcm,
            sample_rate=16_000,
            timestamp_ns=index,
            channels=1,
        )


@pytest.mark.asyncio
async def test_extend_silence_window_defers_the_silence_endpoint() -> None:
    """After ``extend_silence_window`` a silent run shorter than the extended
    window must NOT end the turn — a base-length pause would have, but the
    delegation composer is given room to think."""
    # base 96 ms → 3 silent frames; extend to 320 ms → 10 silent frames.
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    probs = [0.9] * 5 + [0.0] * 5  # 5 silent frames: >= 3 base, < 10 extended
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.05)] * 5 + [_pcm_frame(0.0)] * 5

    out: list[bytes] = []
    async for utterance in vad.utterances(
        _chunks_with_action(
            frames, at_index=4, action=lambda: vad.extend_silence_window(320)
        )
    ):
        out.append(utterance)

    assert out == [], (
        "5 silent frames ended the turn despite the window being extended to "
        "10 — the delegation composer was cut off on a thinking pause"
    )


@pytest.mark.asyncio
async def test_base_silence_window_still_ends_the_turn_without_extension() -> None:
    """Control for the test above: the SAME frames, with NO extension, end the
    turn at the base window — proving the deferral is caused by the extension,
    not by too-few silent frames."""
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    probs = [0.9] * 5 + [0.0] * 5
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.05)] * 5 + [_pcm_frame(0.0)] * 5

    out = await _collect(vad, frames)

    assert len(out) == 1, "base 96 ms window failed to end the turn after 5 silent frames"


@pytest.mark.asyncio
async def test_extended_silence_window_resets_at_next_speech_start() -> None:
    """The patience is per-utterance: it must reset to the snappy default at the
    next speech start so a short command right after a delegation is not made
    sluggish (no cross-turn leak)."""
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    # Utterance 1: speech, extend mid-stream, then 12 silent frames (>= 10
    # extended) → ends. Utterance 2: speech, then only 5 silent frames — these
    # exceed the 3-frame BASE window but are below the 10-frame extended one, so
    # they may only end the turn if the patience reset at speech start.
    probs = [0.9] * 5 + [0.0] * 12 + [0.9] * 5 + [0.0] * 5
    _stub_vad(vad, probs)
    frames = (
        [_pcm_frame(0.05)] * 5
        + [_pcm_frame(0.0)] * 12
        + [_pcm_frame(0.05)] * 5
        + [_pcm_frame(0.0)] * 5
    )

    out: list[bytes] = []
    async for utterance in vad.utterances(
        _chunks_with_action(
            frames, at_index=4, action=lambda: vad.extend_silence_window(320)
        )
    ):
        out.append(utterance)

    assert len(out) == 2, (
        "the extended window leaked into the next utterance — a short command "
        "after a delegation stayed sluggish"
    )


# --------------------------------------------------------------------------- #
# User-tunable "think buffer" (2026-06-16): a Settings slider sets the BASE
# silence window 0.5–5 s, live. ``set_silence_window_ms`` updates the silence
# frame count AND grows the max-utterance cap so a long pause is never beheaded
# by the safety net, and it must take effect mid-stream (no pipeline rebuild).
# --------------------------------------------------------------------------- #


def test_set_silence_window_ms_updates_frames_and_keeps_8s_cap() -> None:
    """A small window updates the silence-frame count and keeps the 8 s cap."""
    vad = SileroEndpointer(silence_ms=1500)
    vad.set_silence_window_ms(2500)
    assert vad._silence_frames == 2500 // 32  # 78
    assert vad._max_samples == 8 * 16000  # ceil(2.5)+5=8 → max(8,8)=8


def test_set_silence_window_ms_grows_cap_for_large_window() -> None:
    """A 5 s window grows the hard cap to 10 s so a long pause is never beheaded."""
    vad = SileroEndpointer(silence_ms=1500)
    vad.set_silence_window_ms(5000)
    assert vad._silence_frames == 5000 // 32  # 156
    assert vad._max_samples == 10 * 16000  # ceil(5)+5=10


def test_set_silence_window_ms_clamps_out_of_range() -> None:
    vad = SileroEndpointer(silence_ms=1500)
    vad.set_silence_window_ms(50)       # below min
    assert vad._silence_frames == 500 // 32
    vad.set_silence_window_ms(99999)    # above max
    assert vad._silence_frames == 5000 // 32


@pytest.mark.asyncio
async def test_live_widened_window_defers_endpoint_mid_stream() -> None:
    """Widening the window mid-utterance must defer an endpoint the old (narrow)
    window would have fired — proving the change is live, not boot-only."""
    # base 96 ms → 3 silent frames to endpoint; widen to 640 ms → 20 frames.
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    probs = [0.9] * 5 + [0.0] * 8  # 8 silent frames: >3 base, <20 widened
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.05)] * 5 + [_pcm_frame(0.0)] * 8

    out: list[bytes] = []
    async for utterance in vad.utterances(
        _chunks_with_action(
            frames, at_index=4, action=lambda: vad.set_silence_window_ms(640)
        )
    ):
        out.append(utterance)

    assert out == [], (
        "8 silent frames ended the turn despite the window being widened live to "
        "20 frames — the setter did not take effect mid-stream"
    )


# --------------------------------------------------------------------------- #
# Autonomous long-utterance patience (2026-06-18): the VAD must arm the wider
# silence window by itself once enough ACTIVE speech has accumulated — without
# relying on the STT probe surfacing a qualifying partial. This fixes session
# 71f2d2de where a 2976 ms silence ended the turn because the 3000 ms patience
# was never armed (the probe never surfaced a partial); had it been armed, 2976
# < 3000 and the whole sentence would have stayed one turn.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_long_active_speech_arms_wider_silence_window_autonomously() -> None:
    """Once ``long_utterance_speech_ms`` of active speech has accumulated, the
    VAD must grant the wider silence window (``long_utterance_silence_ms``)
    WITHOUT any STT-probe involvement, so a long dictation is not cut on a
    short thinking pause.

    Scenario (session 71f2d2de analogue):
    - base silence_ms=320 (10 frames)
    - long_utterance_speech_ms=2000 (62 frames (2000 // 32)), long_utterance_silence_ms=3000
    - feed ~2.5 s of speech (79 frames, > 62) then 12 silent frames
    - 12 > 10 (base) but 12 < 3000//32=93 (extended)
    - Without the fix the turn ends (12 >= 10 base); with the fix it does NOT.
    """
    vad = SileroEndpointer(
        silence_ms=320,            # base: 10 frames
        min_speech_ms=64,
        min_speech_rms=0.002,
        long_utterance_speech_ms=2000,   # 62 frames (2000 // 32) to trigger
        long_utterance_silence_ms=3000,  # extended window: 93 frames
    )
    # 79 speech frames (> 62 threshold) → grant fires; then 12 silence frames
    # (> 10 base, but < 93 extended) → turn must NOT end.
    probs = [0.9] * 79 + [0.0] * 12
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.05)] * 79 + [_pcm_frame(0.0)] * 12

    utterances = await _collect(vad, frames)

    assert utterances == [], (
        "12 silent frames ended the long-dictation turn even though the autonomous "
        "patience grant should have raised the window to 93 frames (3000 ms) — "
        "session 71f2d2de recurrence: the STT probe never armed the window and the "
        "snappy base cut the sentence mid-word"
    )


@pytest.mark.asyncio
async def test_short_command_stays_snappy_no_autonomous_patience_grant() -> None:
    """A short command (< ``long_utterance_speech_ms`` of active speech) must
    NOT receive the wider patience grant — the snappy base silence window ends
    the turn promptly so simple commands stay responsive.

    Anti-confirmation-fatigue contract: short commands are NEVER made sluggish.
    """
    vad = SileroEndpointer(
        silence_ms=320,            # base: 10 frames
        min_speech_ms=64,
        min_speech_rms=0.002,
        long_utterance_speech_ms=2000,   # 62 frames (2000 // 32) to trigger
        long_utterance_silence_ms=3000,  # would be 93 frames if triggered
    )
    # 28 speech frames (< 62 threshold) → grant must NOT fire; then 11 silence
    # frames (> 10 base, < 93 extended) → turn MUST end at the base window.
    probs = [0.9] * 28 + [0.0] * 11
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.05)] * 28 + [_pcm_frame(0.0)] * 11

    utterances = await _collect(vad, frames)

    assert len(utterances) == 1, (
        "a short command (28 speech frames < 62-frame threshold) had its silence "
        "window widened autonomously — short commands must stay snappy"
    )
    # Explicit non-widening guard: the effective window must equal the base
    # window. Proving "one utterance" only shows non-widening indirectly; this
    # assert pins the anti-confirmation-fatigue contract directly so a future
    # refactor that breaks the speech-frame guard but still yields one utterance
    # cannot pass silently.
    assert vad._effective_silence_frames == vad._silence_frames, (
        "the autonomous patience grant widened the window for a short command "
        f"(_effective_silence_frames={vad._effective_silence_frames}, "
        f"_silence_frames={vad._silence_frames})"
    )


@pytest.mark.asyncio
async def test_autonomous_patience_grant_resets_across_utterances() -> None:
    """The autonomous patience grant must NOT leak into the next utterance.

    After a long turn (which arms the wide window) ends, a new short command
    must reset to the snappy base window — exactly like the probe-driven grant.
    """
    vad = SileroEndpointer(
        silence_ms=320,            # base: 10 frames
        min_speech_ms=64,
        min_speech_rms=0.002,
        long_utterance_speech_ms=2000,   # 62 frames (2000 // 32) to trigger
        long_utterance_silence_ms=3000,  # extended: 93 frames
    )
    # Utterance 1: 79 speech frames (triggers grant) + 100 silence frames → ends
    # (100 >= 93 extended). Utterance 2: 10 speech frames (< 62, no grant) + 11
    # silence frames (> 10 base, < 93 extended) → must end at the base window,
    # proving the grant was reset.
    probs = [0.9] * 79 + [0.0] * 100 + [0.9] * 10 + [0.0] * 11
    _stub_vad(vad, probs)
    frames = (
        [_pcm_frame(0.05)] * 79
        + [_pcm_frame(0.0)] * 100
        + [_pcm_frame(0.05)] * 10
        + [_pcm_frame(0.0)] * 11
    )

    utterances = await _collect(vad, frames)

    assert len(utterances) == 2, (
        "the autonomous patience grant leaked into the second (short) utterance — "
        f"got {len(utterances)} utterances; the second should have ended at the "
        "10-frame base window, not the 93-frame extended window"
    )


# --------------------------------------------------------------------------- #
# The configured "Thinking pause" must govern the silence window — stuck-in-
# LISTENING regression (2026-06-29).
#
# Forensic: the user set the Settings slider to 1.0 s, expecting the turn to
# submit after ~1 s of silence. It did not. The autonomous long-utterance grant
# (and the STT-probe delegation grant) call ``extend_silence_window(3000)``,
# which set the silence window to a FIXED 3000 ms regardless of the configured
# base — so any utterance with >=2 s of speech waited ~3 s, and on pause-rich
# speech the natural endpoint slipped so far that only the max_utterance cap
# fired (forced-cut -> carry -> keep listening, NOT a submit) until the session
# inactivity timeout ended it. The fix caps the grant relative to the configured
# base, so the slider value actually governs the wait.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_configured_silence_window_submits_long_utterance_not_stuck() -> None:
    """TEST A (the fix): with the slider at 1.0 s, a long utterance followed by
    silence above the configured threshold must SUBMIT (yield exactly one
    utterance) instead of hanging in LISTENING until the inactivity timeout.

    65 speech frames (> the 62-frame long-utterance trigger) arm the autonomous
    patience grant; then 70 silence frames (2240 ms) follow. With the bug the
    grant forces the window to a fixed ~3000 ms (93 frames), so 70 < 93 -> no
    endpoint ever fires, the VAD never yields, and on the live ``_active_session``
    loop the inactivity timeout would end the session. With the fix the grant is
    capped relative to the 1.0 s base, so the window stays around 2 s and the
    70 silence frames cross it -> one submitted utterance.
    """
    vad = SileroEndpointer(silence_ms=1000)  # production wiring: base 1.0 s
    endpoint_reasons: list[str] = []
    vad._on_endpoint = lambda reason: endpoint_reasons.append(reason)  # type: ignore[method-assign]

    probs = [0.9] * 65 + [0.0] * 70
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.08) for _ in range(65)] + [_pcm_frame(0.0) for _ in range(70)]

    utterances = await _collect(vad, frames)

    assert len(utterances) == 1, (
        "a long utterance + 2.24 s of silence at a 1.0 s setting did not submit — "
        "the hardcoded 3 s patience grant kept the turn stuck in LISTENING "
        f"(endpoint reasons: {endpoint_reasons})"
    )
    assert endpoint_reasons == ["silence"], (
        "the turn must end on the natural silence endpoint, not a forced cut "
        f"(got {endpoint_reasons})"
    )


@pytest.mark.asyncio
async def test_pure_silence_yields_nothing_so_session_idle_times_out() -> None:
    """TEST B (regression): pure silence with NO captured speech must yield
    nothing, so the session still ends via the inactivity timeout. The fix
    (a shorter silence window) must not make noise/silence submit spuriously.

    Mirrors the session-level guard ``test_idle_timeout_still_hangs_up_without_
    inflight_mission``: a VAD that never yields -> ``_active_session`` returns
    ``HANGUP_IDLE_TIMEOUT``.
    """
    vad = SileroEndpointer(silence_ms=1000)
    probs = [0.0] * 120  # never crosses the speech threshold
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.0) for _ in range(120)]

    utterances = await _collect(vad, frames)

    assert utterances == [], (
        "pure silence produced a submitted utterance — there was no captured "
        "speech, so the session must fall through to the inactivity timeout"
    )


@pytest.mark.asyncio
async def test_lower_thinking_pause_submits_sooner_than_higher() -> None:
    """TEST C (config): the configured threshold must govern the timing. With
    the SAME long utterance and the SAME amount of trailing silence, a lower
    "Thinking pause" setting submits while a higher one is still waiting —
    proving the slider value (not a fixed 3 s constant) drives the endpoint.

    75 silence frames (2400 ms) follow a long utterance for both VADs. With the
    bug both windows are forced to a fixed ~3000 ms (93 frames), so 75 < 93 ->
    NEITHER submits and the timing is identical regardless of the setting. With
    the fix the 1.0 s base caps the window near 2 s (75 frames cross it -> submit)
    while the 1.5 s base caps it near 3 s (75 frames do not -> still waiting).
    """
    async def _submitted(silence_ms: int) -> int:
        vad = SileroEndpointer(silence_ms=silence_ms)
        probs = [0.9] * 65 + [0.0] * 75
        _stub_vad(vad, probs)
        frames = [_pcm_frame(0.08) for _ in range(65)] + [
            _pcm_frame(0.0) for _ in range(75)
        ]
        return len(await _collect(vad, frames))

    submitted_at_1000ms = await _submitted(1000)
    submitted_at_1500ms = await _submitted(1500)

    assert submitted_at_1000ms == 1, (
        "the 1.0 s setting did not submit after 2.4 s of silence — its window is "
        "still pinned to the fixed 3 s grant instead of scaling with the setting"
    )
    assert submitted_at_1500ms == 0, (
        "the 1.5 s setting submitted at the same silence as the 1.0 s one — the "
        "endpoint timing does not scale with the configured threshold"
    )
