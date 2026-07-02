"""Rolling-window Whisper wake detection — robust wake without a VAD dependency.

Unlike `whisper_wake.py` (which waits for a VAD endpoint and fails on quiet
mics): here a 2.5-second ring buffer of audio is held and transcribed by
Whisper every 500 ms. If "jarvis" shows up in the transcript — trigger.

Advantages:
- No VAD dependency → also works at a low mic level
- Triggers immediately (500 ms polling interval), not only after speech ends
- Uses Whisper (natively German-capable) → no English-training bias

Disadvantages:
- Higher GPU load (Whisper runs continuously instead of only at utterance end)
- On an RTX 5070 Ti with distil-large-v3: ~80-150 ms per 2.5-second transcription
  = ~20 % GPU usage at a 500 ms poll interval

Parameters:
- `window_s`: buffer length (default 2.5 s — long enough for "Hey Jarvis")
- `poll_interval_s`: how often we transcribe (default 0.5 s)
- `cooldown_s`: don't trigger again immediately after a trigger (default 2 s)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import wave
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.audio.capture import pcm_bytes_to_np
from jarvis.core.protocols import AudioChunk
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider, TranscribeBusy

# The strict "hey/hi/hallo + jarv-stem" pattern now lives in wake_constants as
# the single source of truth (the prefix verifier re-exports the same object),
# so the two STT wake paths can never drift apart (BUG-008). Re-exported here
# under the historical ``DEFAULT_PATTERN`` name so existing call sites and tests
# keep working. ``pattern=`` also accepts a ``WakeMatcher`` (duck-types
# ``.search().group(0)``) so a custom wake phrase can drive this backstop.
from jarvis.speech.wake_constants import JARVIS_WAKE_PATTERN as DEFAULT_PATTERN

log = logging.getLogger("jarvis.wake.rolling")


# Watchdog directory for debug WAVs
DEBUG_DIR = Path(os.environ.get("JARVIS_DEBUG_DIR", "./data/wake_debug"))

# Production default is OFF. The watchdog WAV dump writes ONE WAV file per
# transcribed wake window SYNCHRONOUSLY inside the poll loop. Left on, it both
# (a) accumulates unbounded — a live box reached 218k files in data/wake_debug/
# 2026-06-29 — and (b) on Windows, writing into a directory that large is slow,
# so the disk I/O lands ON the wake hot path and adds latency to every poll
# (the user's "no delay" requirement). Opt in for debugging with
# JARVIS_WAKE_DEBUG_WAVS=1; otherwise the wake path never touches the disk.
_DEBUG_WAVS_ENV = os.environ.get("JARVIS_WAKE_DEBUG_WAVS", "").strip().lower() in (
    "1", "true", "yes", "on",
)

# After this many CONSECUTIVE transcription failures (a timeout = the local
# Whisper hung, or a "busy" skip because a prior call is wedged holding the
# model), rebuild the wake model fresh via ``stt.recover()``. Forensic
# 2026-06-29: a custom wake ("Hey Nico") went dead for 2 HOURS — every transcribe
# timed out at 8 s, abandoned, retried, hung again, forever; an app restart did
# not even clear it. The timeout only BOUNDS a hang; it never RECOVERS. A run of
# failures with zero successes is the wedge signature (a legitimate VAD-probe
# overlap clears in 1-2 polls and resets the counter on the next success).
# 2026-06-30 (live logs showed the base/cpu model wedging dozens of times a day,
# each dead window swallowing spoken wakes -> "say it 2-3 times"): lowered 5 -> 2
# so the deaf window is as short as possible. Two consecutive failures with zero
# successes in between is already an unambiguous wedge; a lone transient overlap
# clears on the very next successful poll and resets the counter, so 2 does not
# fire spuriously.
_WEDGE_RECOVER_AFTER_FAILS = 2

# Hard cap on a CONTINUOUS run of ``TranscribeBusy`` polls before the in-flight
# call is declared truly hung and the model is rebuilt. A busy poll right after
# an abandoned timeout is the SAME still-running call, not a second independent
# failure — the old accounting counted it toward ``_WEDGE_RECOVER_AFTER_FAILS``,
# so ONE transcription slower than the 8 s cap (routine under boot/CPU load,
# p95 was measured at 5.3 s under contention) tore down a healthy model, and
# the lazy cold rebuild inside the NEXT poll's 8 s timeout re-wedged — a
# self-perpetuating deaf cycle (live log 2026-07-02 08:21-08:26: three recover
# cycles in 2 minutes while the user was audibly speaking). 20 s tolerates any
# slow-but-alive call (which frees the lock and resets the streak on return)
# while a genuine BUG-036 hang (un-cancellable native call) is still recovered
# in bounded time (~8 s timeout + this cap).
_BUSY_HANG_RECOVER_S = 20.0


def _save_wav(pcm_bytes: bytes, sample_rate: int, path: Path) -> None:
    """Writes int16 PCM as a valid WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


def _segment_no_speech_probs(transcript: Any) -> list[float]:
    probs: list[float] = []
    for seg in getattr(transcript, "segments", ()) or ():
        if not isinstance(seg, dict):
            continue
        value = seg.get("no_speech_prob")
        if value is None:
            continue
        try:
            probs.append(float(value))
        except (TypeError, ValueError):
            continue
    return probs


def _reliable_wake_transcript(
    transcript: Any,
    *,
    min_confidence: float,
    max_no_speech_prob: float,
) -> bool:
    try:
        confidence = float(getattr(transcript, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < min_confidence:
        return False
    return not any(
        prob > max_no_speech_prob for prob in _segment_no_speech_probs(transcript)
    )


class RollingWhisperWake:
    """Rolling-Window Wake-Detection per Whisper-Transkription."""

    def __init__(
        self,
        stt: FasterWhisperProvider,
        # Either a compiled regex or a WakeMatcher — both expose
        # ``.search(text)`` returning an object with ``.group(0)``.
        pattern: Any = DEFAULT_PATTERN,
        window_s: float = 1.8,        # shorter = less silence share = higher avg RMS
        # 2026-06-30 ("~0.5 s delay"): 0.3 -> 0.2 so a spoken custom wake reaches
        # the bar within one snappier poll. The gates skip silence cheaply, so the
        # extra polls only transcribe when a window actually passes the audio
        # gates — negligible extra cost, a visibly faster reaction.
        poll_interval_s: float = 0.2,
        cooldown_s: float = 5.0,      # longer cooldown → less over-triggering
        sample_rate: int = 16_000,
        # 2026-04-22 (3rd iteration): RMS/peak gates back down to low. The
        # user's headset input level is very quiet (typically rms 0.01-0.02
        # at normal speaking volume). Higher gates blocked genuine "Hey Jarvis".
        # Protection against hallucinations now comes from the pattern alone:
        # Whisper hallucinates "JARVIS.", "Vielen Dank.", "Thank you" — none of  # i18n-allow
        # these match our pattern (only "hey/hi/hallo" + the jarv- stem).
        # Whisper does get called a bit more often as a result (more GPU load),
        # but the trigger behavior is correct — exactly what the user wants.
        min_rms: float = 0.003,
        # 2026-06-29 (mission "wake only triggers when shouting"): the raw peak
        # gate runs BEFORE transcription on a quiet mic, so a normal-volume
        # custom wake ("Hey Nico") whose window peaks below the legacy 0.02 was
        # dropped silently — only a shout cleared it. 2026-06-30: lowered further
        # 0.012 -> 0.008 because a downloader on an even quieter built-in laptop
        # mic still peaked below 0.012. 0.008 still sits well above the ~0.0046
        # idle-hiss level (pinned gated by test_stats_count_a_sub_peak_window),
        # and the ``min_rms`` silence guard (pinned >= 0.003) plus the confidence +
        # no_speech + pattern gates remain the real false-positive guards on
        # whatever does reach Whisper.
        min_peak: float = 0.008,
        save_debug_wavs: bool = False,  # Watchdog-Modus — OFF in prod (env opt-in)
        heartbeat_interval_s: float = 3.0,
        # Peak normalization instead of a fixed gain: measures the audio peak,
        # dynamically applies whatever gain is needed to reach -3 dBFS.
        # Substitutes for a missing Windows/hardware mic boost WITHOUT clipping.
        # During silence/quiet noise the gain is capped (max_gain_db).
        target_peak_dbfs: float = -3.0,
        max_gain_db: float = 40.0,
        language: str = "de",
        # faster-whisper's exp(avg_logprob) score is harsh on 1-2 word wake
        # chunks: live, cleanly-heard custom-name wakes land at ~0.28-0.52 (real
        # samples 2026-06-23: "Alex." 0.318, "Hey Ruhm" 0.365, "Hey Alex" 0.52).
        # A 0.45 floor rejected EVERY genuine wake (142 rejects / 0 accepts in one
        # evening). That floor was built to suppress *prompt-bias* hallucinations,
        # but the bias is now disabled (build_wake_whisper passes initial_prompt
        # =None), so the pattern itself is the hallucination guard — a random
        # mis-hear does not match the specific wake phrase — and the
        # ``max_no_speech_prob`` gate below still rejects silence/noise. Keep only
        # a low sanity floor that still drops a near-zero-confidence transcript
        # (regression: a 0.2-confidence match must stay rejected).
        # 2026-06-29 (mission "wake fails repeatedly / only when shouting"): 0.28
        # sat at the very BOTTOM of the measured genuine-wake band (0.28-0.52), so
        # a quiet-but-correct wake under-scored it and was rejected — louder =
        # higher confidence = accepted, which IS the "only when shouting" symptom.
        # Lowered to 0.22: still strictly above the 0.2 hallucination floor the
        # regression test pins, but it no longer clips the quiet tail of genuine
        # wakes. The phrase pattern + no_speech gate remain the real guards.
        min_wake_confidence: float = 0.22,
        max_no_speech_prob: float = 0.6,
        # Hard ceiling on a SINGLE transcription. Live forensic 2026-06-29
        # (data/jarvis_desktop.log): the local faster-whisper ``transcribe_pcm``
        # hung mid-session (no error, no return) and, with no cap, the poll loop
        # blocked on that one ``await`` forever — the chunk consumer stayed alive
        # (audio kept flowing, max-rms up to 0.27 while the user spoke) but ZERO
        # transcripts were produced for 12 min and the custom wake word ("Hey
        # Nico") was permanently dead. A genuine transcription of a ~1.8 s window
        # is ~0.1 s (GPU) to ~1 s (CPU base), so a multi-second cap never cuts a
        # real one but lets the loop ABANDON a hung call and re-poll fresh audio
        # (self-healing — the "no dead state blocks waking" guarantee). Mirrors
        # the OWW prefix-verifier's _WAKE_VERIFY_TIMEOUT_S. Set high enough to
        # tolerate a slow/loaded CPU box (€5-VPS doctrine); <= 0 disables the cap.
        transcribe_timeout_s: float = 8.0,
        # Boot serialisation (TTU forensic 2026-07-02): how long to wait for the
        # provider's one-off warm-up (owned by the pipeline's deferred loader)
        # before this loop warms the model itself as a fallback owner. Polling
        # transcribe WHILE the model loads used to cascade: 8 s timeout ->
        # TranscribeBusy -> self-heal recover() threw the half-loaded model away
        # -> reload from scratch under the boot CPU storm (114.7 s for a ~4 s
        # load). The poll phase therefore starts only on a warm model.
        warm_wait_fallback_s: float = 20.0,
        # How long a CONTINUOUS TranscribeBusy streak may run before the
        # in-flight call counts as truly hung (see _BUSY_HANG_RECOVER_S).
        busy_hang_recover_s: float = _BUSY_HANG_RECOVER_S,
    ) -> None:
        self._stt = stt
        self._pattern = pattern
        self._warm_wait_fallback_s = float(warm_wait_fallback_s)
        self._busy_hang_recover_s = float(busy_hang_recover_s)
        self._window_samples = int(window_s * sample_rate)
        self._poll_interval_s = poll_interval_s
        self._cooldown_s = cooldown_s
        self._sample_rate = sample_rate
        self._min_rms = min_rms
        # Caller flag OR the env opt-in. Default OFF so the wake poll loop never
        # does synchronous disk I/O (latency) or accretes a huge WAV dir in prod.
        self._save_debug_wavs = bool(save_debug_wavs) or _DEBUG_WAVS_ENV
        self._heartbeat_interval_s = heartbeat_interval_s
        self._target_peak = float(10.0 ** (target_peak_dbfs / 20.0))  # -3 dBFS ≈ 0.707
        self._max_gain_factor = float(10.0 ** (max_gain_db / 20.0))    # 40 dB = 100x
        self._min_peak = min_peak
        # Pin wake transcription to a fixed language — auto-detect on 1.8s
        # chunks often falsely flips to EN (user speaks DE, Whisper
        # hallucinates "Thank you"). None = auto (not recommended).
        self._language: str | None = language
        self._min_wake_confidence = min_wake_confidence
        self._max_no_speech_prob = max_no_speech_prob
        self._transcribe_timeout_s = float(transcribe_timeout_s)
        # Statistics for the heartbeat
        self._chunks_seen = 0
        self._total_bytes = 0
        self._max_rms = 0.0
        self._last_transcript = ""
        self._last_heartbeat_t = time.time()
        # Per-session debug counters (mirrors OpenWakeWordProvider.stats() so the
        # two wake paths report the same way). They make the stt_match path's
        # "wake never fires / sometimes stops entirely" diagnosable: a user can
        # see how many windows were evaluated, how many were too quiet to even
        # transcribe (gated_peak / gated_rms), how many reached Whisper, and why
        # each transcript was dropped — instead of a silent dead listener.
        self._stat_windows_polled = 0
        self._stat_gated_rms = 0
        self._stat_gated_peak = 0
        self._stat_transcribed = 0
        self._stat_empty = 0
        self._stat_rejected_unreliable = 0
        self._stat_rejected_no_match = 0
        self._stat_matched = 0
        self._stat_suppressed_cooldown = 0

    def stats(self) -> dict[str, int]:
        """Snapshot of this listen session's wake-evaluation counters.

        Keys: ``windows_polled`` (snapshots that reached the audio gates),
        ``gated_rms`` / ``gated_peak`` (dropped as silence / sub-speech BEFORE
        Whisper), ``transcribed`` (Whisper calls), ``empty`` (blank transcript),
        ``rejected_unreliable`` (confidence/no_speech gate), ``rejected_no_match``
        (transcript did not contain the wake phrase), ``matched`` (wake yielded),
        ``suppressed_cooldown`` (in the debounce window). Surfaced in the
        heartbeat log; the analogue of ``OpenWakeWordProvider.stats()``.
        """
        return {
            "windows_polled": self._stat_windows_polled,
            "gated_rms": self._stat_gated_rms,
            "gated_peak": self._stat_gated_peak,
            "transcribed": self._stat_transcribed,
            "empty": self._stat_empty,
            "rejected_unreliable": self._stat_rejected_unreliable,
            "rejected_no_match": self._stat_rejected_no_match,
            "matched": self._stat_matched,
            "suppressed_cooldown": self._stat_suppressed_cooldown,
        }

    async def detect(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[str]:
        """Consumes audio chunks, yields the matched keyword on a hit.

        The chunk consumer and the (slow, blocking) Whisper transcription run as
        TWO concurrent tasks. The consumer keeps the rolling ring-buffer pinned
        to the freshest ``window_s`` of audio; a separate poll loop snapshots the
        current window every ``poll_interval_s`` and transcribes THAT.

        Why two tasks (forensic 2026-06-22): the old single loop did
        ``await transcribe_pcm`` *inside* the consume loop, so while a CPU "base"
        transcription ran for ~0.5-1 s no new chunks were pulled. They backed up
        in the upstream fanout queue (observed ``wsp_q=100``) and every following
        transcription ran on ~3 s-stale audio — the "huge lag". With
        the consumer decoupled, the buffer is always live and the transcription
        sees the newest window, never a backlog.
        """
        # Ring-Buffer: float32 samples im [-1, 1] Bereich. Held in a 1-element
        # list so the consumer closure mutates it in place without ``nonlocal``.
        buffer: deque[np.ndarray] = deque()
        buf_len = [0]
        stopped = asyncio.Event()

        async def _consume() -> None:
            """Drain audio into the rolling window — fast, never blocks on STT."""
            try:
                async for chunk in chunks:
                    samples = pcm_bytes_to_np(chunk.pcm)
                    buffer.append(samples)
                    buf_len[0] += len(samples)

                    # Update heartbeat statistics (live RMS per chunk)
                    self._chunks_seen += 1
                    self._total_bytes += len(chunk.pcm)
                    chunk_rms = float(np.sqrt(np.mean(samples * samples) + 1e-12))
                    if chunk_rms > self._max_rms:
                        self._max_rms = chunk_rms

                    # Emit the heartbeat regularly — even when Whisper matched nothing
                    now_hb = time.time()
                    if now_hb - self._last_heartbeat_t >= self._heartbeat_interval_s:
                        dbfs = 20.0 * np.log10(max(self._max_rms, 1e-12))
                        log.info(
                            "💓 wake-heartbeat: chunks=%d bytes=%dKB "
                            "max-rms=%.4f (%.1f dBFS) last-transcript=%r | "
                            "windows=%d gated[rms=%d peak=%d] transcribed=%d "
                            "rejected[unreliable=%d no_match=%d] matched=%d "
                            "(conf_floor=%.2f peak_gate=%.3f)",
                            self._chunks_seen,
                            self._total_bytes // 1024,
                            self._max_rms,
                            dbfs,
                            self._last_transcript[:80],
                            self._stat_windows_polled,
                            self._stat_gated_rms,
                            self._stat_gated_peak,
                            self._stat_transcribed,
                            self._stat_rejected_unreliable,
                            self._stat_rejected_no_match,
                            self._stat_matched,
                            self._min_wake_confidence,
                            self._min_peak,
                        )
                        self._chunks_seen = 0
                        self._total_bytes = 0
                        self._max_rms = 0.0
                        self._last_heartbeat_t = now_hb

                    # Discard older samples when the buffer gets too long
                    while buf_len[0] > self._window_samples:
                        oldest = buffer[0]
                        overflow = buf_len[0] - self._window_samples
                        if len(oldest) <= overflow:
                            buffer.popleft()
                            buf_len[0] -= len(oldest)
                        else:
                            buffer[0] = oldest[overflow:]
                            buf_len[0] -= overflow
            finally:
                stopped.set()

        consumer = asyncio.create_task(_consume(), name="rolling-whisper-consume")
        last_trigger_t = 0.0
        # Self-heal counter: consecutive transcribe failures with no success.
        # Reset on any successful transcription. Only failures of DISTINCT
        # calls count — a TranscribeBusy right after an abandoned timeout is
        # the SAME in-flight call still running, tracked separately via
        # ``busy_since`` (see the busy handler below and _BUSY_HANG_RECOVER_S).
        consecutive_fail = 0
        # Start of the current continuous TranscribeBusy streak (None = the
        # last poll was not busy). A streak longer than
        # ``self._busy_hang_recover_s`` is a TRUE hang -> rebuild.
        busy_since: float | None = None

        # Boot serialisation state — see the warm gate inside the poll loop.
        warm_wait_t0 = time.time()
        warm_wait_logged = False
        fallback_warmed = False
        # Set when recover() dropped the model MID-SESSION: the warm gate then
        # re-warms immediately (this loop owns it — the boot deferred loader
        # only runs once) instead of lazily rebuilding INSIDE the next poll's
        # transcribe timeout, which under load re-wedged and cascaded (live
        # log 2026-07-02 08:21-08:26).
        rewarm_owed = False

        def _recover_wedged(reason: str) -> None:
            nonlocal busy_since, consecutive_fail, rewarm_owed
            recover = getattr(self._stt, "recover", None)
            if callable(recover):
                log.error(
                    "rolling-whisper: %s — rebuilding the wedged wake model "
                    "(self-heal, no restart).",
                    reason,
                )
                try:
                    recover()
                except Exception as exc:  # noqa: BLE001 — heal must never crash
                    log.warning("rolling-whisper: model recover() failed: %s", exc)
                rewarm_owed = True
            busy_since = None
            consecutive_fail = 0

        def _note_transcribe_fail() -> int:
            nonlocal consecutive_fail
            consecutive_fail += 1
            failed = consecutive_fail
            if consecutive_fail >= _WEDGE_RECOVER_AFTER_FAILS:
                _recover_wedged(
                    f"{consecutive_fail} consecutive transcribe failures"
                )
            return failed

        try:
            while not stopped.is_set():
                # Wall-clock poll cadence — independent of the chunk arrival rate
                # and, crucially, of how long the previous transcription took.
                await asyncio.sleep(self._poll_interval_s)

                # --- Boot serialisation: poll only a WARM model -----------
                # The pipeline's deferred loader owns the one-off model
                # warm-up. Poking ``transcribe_pcm`` while that load is in
                # flight used to cascade (8 s timeout -> TranscribeBusy ->
                # recover() threw the half-loaded model away -> reload under
                # the boot storm; 114.7 s instead of ~4 s, TTU forensic
                # 2026-07-02). Skip the transcribe phase (and the self-heal
                # fail counting) until ``is_warm``; if nobody warms the model
                # within the fallback window (unusual wiring), warm it from
                # HERE once — exactly one loader either way. Providers
                # without the flag (fakes, cloud STT) count as warm. The
                # audio consumer keeps the rolling buffer live throughout.
                if not getattr(self._stt, "is_warm", True):
                    if rewarm_owed:
                        # Mid-session self-heal: recover() just dropped the
                        # model. Rebuild + prime it HERE, off the transcribe
                        # timeout, so the next poll meets a hot model instead
                        # of a cold load racing an 8 s deadline (the cascade).
                        rewarm_owed = False
                        warm = getattr(self._stt, "warm_up", None)
                        if callable(warm):
                            t_rewarm = time.time()
                            log.info(
                                "rolling-whisper: re-warming the rebuilt wake "
                                "model off the poll path (mid-session self-heal)."
                            )
                            try:
                                await asyncio.to_thread(warm)
                                log.info(
                                    "rolling-whisper: rebuilt wake model warm "
                                    "in %.1f s — polling resumes.",
                                    time.time() - t_rewarm,
                                )
                            except Exception as exc:  # noqa: BLE001
                                log.warning(
                                    "rolling-whisper: re-warm failed (%s) — "
                                    "lazy load on the next poll.",
                                    exc,
                                )
                    elif (
                        not fallback_warmed
                        and time.time() - warm_wait_t0 > self._warm_wait_fallback_s
                    ):
                        fallback_warmed = True
                        log.info(
                            "rolling-whisper: wake model still cold after %.0f s "
                            "— warming it from the poll loop (fallback owner).",
                            self._warm_wait_fallback_s,
                        )
                        warm = getattr(self._stt, "warm_up", None)
                        if callable(warm):
                            try:
                                await asyncio.to_thread(warm)
                            except Exception as exc:  # noqa: BLE001 — lazy load still works
                                log.warning(
                                    "rolling-whisper: fallback warm-up failed: %s",
                                    exc,
                                )
                    else:
                        if not warm_wait_logged:
                            warm_wait_logged = True
                            log.info(
                                "rolling-whisper: waiting for the wake model "
                                "warm-up before polling (buffer keeps filling)."
                            )
                        continue
                if warm_wait_logged:
                    warm_wait_logged = False
                    log.info(
                        "rolling-whisper: wake model warm — polling starts "
                        "(waited %.1f s).",
                        time.time() - warm_wait_t0,
                    )

                now = time.time()
                # Cooldown after the last trigger
                if now - last_trigger_t < self._cooldown_s:
                    self._stat_suppressed_cooldown += 1
                    continue
                # Not enough audio in the buffer yet
                if buf_len[0] < self._sample_rate:  # mind. 1 Sek
                    continue

                # Snapshot the freshest window. ``list(buffer)`` + concat run
                # synchronously (no await), so the consumer cannot interleave a
                # mutation mid-snapshot in this single-threaded loop.
                if not buffer:
                    continue
                audio_np = np.concatenate(list(buffer))
                if len(audio_np) < self._sample_rate:
                    continue
                # A full window reached the audio gates — this is one wake
                # evaluation attempt (the denominator for the gate counters).
                self._stat_windows_polled += 1

                # Volume check (RMS) — no Whisper call during silence
                rms = float(np.sqrt(np.mean(audio_np * audio_np) + 1e-12))
                if rms < self._min_rms:
                    self._stat_gated_rms += 1
                    continue

                # Peak gate: don't bother Whisper at all on pure noise
                peak = float(np.max(np.abs(audio_np)))
                if peak < self._min_peak:
                    # No Whisper call — too quiet for speech. Log the measured
                    # peak so "wake stopped working" on a quiet mic is visible as
                    # "your audio peaks below the gate", not silent nothing.
                    self._stat_gated_peak += 1
                    log.debug(
                        "rolling-whisper: window gated (peak=%.4f < %.4f) — too quiet",
                        peak, self._min_peak,
                    )
                    continue

                # Whisper call with peak normalization (dynamic gain)
                try:
                    if peak > 1e-6:
                        # Compute the gain needed to reach the target peak, but cap it
                        gain = min(self._target_peak / peak, self._max_gain_factor)
                    else:
                        gain = 1.0
                    boosted = audio_np * gain
                    applied_db = 20.0 * np.log10(max(gain, 1e-12))
                    pcm_bytes = (
                        np.clip(boosted, -1.0, 1.0) * 32767.0
                    ).astype(np.int16).tobytes()
                    log.debug("whisper-gain applied=%.1f dB (peak-in=%.3f)", applied_db, peak)
                    # Bounded transcription: a hung local-Whisper call must not
                    # freeze the poll loop forever (the 12-min silent-wedge
                    # forensic). On timeout we abandon THIS call and re-poll fresh
                    # audio so the wake self-heals instead of dying. timeout<=0
                    # disables the cap. We use ``asyncio.timeout`` (3.11+), NOT
                    # ``asyncio.wait_for``: wait_for SWALLOWS an external
                    # cancellation when the inner coroutine completes in the same
                    # tick (a fast/instant STT), which would make detect() ignore
                    # its own ``aclose``/cancel and loop forever on shutdown.
                    # ``asyncio.timeout`` raises TimeoutError only on ITS deadline
                    # and lets an external CancelledError propagate untouched.
                    if self._transcribe_timeout_s > 0:
                        async with asyncio.timeout(self._transcribe_timeout_s):
                            transcript = await self._stt.transcribe_pcm(
                                pcm_bytes, language=self._language
                            )
                    else:
                        transcript = await self._stt.transcribe_pcm(
                            pcm_bytes, language=self._language
                        )
                    self._stat_transcribed += 1
                    consecutive_fail = 0  # a success clears the wedge streak
                    busy_since = None
                except TimeoutError:
                    # A call STARTED (the lock was free), overran the cap and
                    # was abandoned — its worker thread keeps running. Any
                    # previous busy streak ended when this call took the lock.
                    busy_since = None
                    log.warning(
                        "Rolling-Whisper transcription aborted after %.1fs "
                        "(hung STT, %d in a row) — re-polling, wake stays alive",
                        self._transcribe_timeout_s,
                        _note_transcribe_fail(),
                    )
                    continue
                except TranscribeBusy:
                    # The SAME in-flight call (usually one an earlier timeout
                    # abandoned) still holds the model — NOT a new failure.
                    # Counting it toward the wedge threshold let ONE
                    # slow-but-alive transcription (>8 s under CPU load) tear
                    # down a healthy model; the lazy cold rebuild inside the
                    # next poll's timeout then re-wedged — the deaf cascade in
                    # the 2026-07-02 live log. Skip the poll. Only a busy
                    # streak longer than ``busy_hang_recover_s`` is a TRUE
                    # hang (BUG-036, un-cancellable native call) -> rebuild.
                    now_busy = time.time()
                    if busy_since is None:
                        busy_since = now_busy
                        log.info(
                            "rolling-whisper: transcription still in flight — "
                            "skipping this poll (not counted as a failure)."
                        )
                    elif now_busy - busy_since >= self._busy_hang_recover_s:
                        _recover_wedged(
                            "in-flight transcription stuck for "
                            f">{self._busy_hang_recover_s:.0f} s (true hang)"
                        )
                    continue
                except Exception as exc:  # noqa: BLE001
                    busy_since = None
                    log.warning(
                        "Rolling-Whisper transcription failed (%d in a row): %s",
                        _note_transcribe_fail(), exc,
                    )
                    continue

                text = transcript.text.strip()
                self._last_transcript = text

                # Watchdog: save the WAV so the user/I can review the recording
                if self._save_debug_wavs:
                    try:
                        pcm_bytes_for_wav = (
                            np.clip(audio_np, -1.0, 1.0) * 32767.0
                        ).astype(np.int16).tobytes()
                        ts = time.strftime("%H%M%S")
                        safe_text = re.sub(r"[^\w\-]+", "_", text[:40]) or "empty"
                        wav_path = DEBUG_DIR / f"wake_{ts}_rms{rms:.3f}_{safe_text}.wav"
                        _save_wav(pcm_bytes_for_wav, self._sample_rate, wav_path)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("WAV save failed: %s", exc)

                if not text:
                    self._stat_empty += 1
                    log.info("rolling-whisper: rms=%.4f text=<empty>", rms)
                    continue

                if not _reliable_wake_transcript(
                    transcript,
                    min_confidence=self._min_wake_confidence,
                    max_no_speech_prob=self._max_no_speech_prob,
                ):
                    self._stat_rejected_unreliable += 1
                    log.info(
                        "rolling-whisper: rejected unreliable wake transcript "
                        "rms=%.4f confidence=%.3f (floor %.2f) no_speech=%r text=%r",
                        rms,
                        float(getattr(transcript, "confidence", 0.0) or 0.0),
                        self._min_wake_confidence,
                        _segment_no_speech_probs(transcript),
                        text,
                    )
                    continue

                log.info("rolling-whisper: rms=%.4f text=%r", rms, text)
                m = self._pattern.search(text)
                if m:
                    self._stat_matched += 1
                    last_trigger_t = now
                    log.info("rolling-whisper: WAKE matched %r in %r", m.group(0), text)
                    yield m.group(0)
                else:
                    self._stat_rejected_no_match += 1
        finally:
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
