"""openWakeWord-Plugin — detektiert "Hey Jarvis" / "Jarvis" im Audio-Stream.

openWakeWord ist ein freies, Open-Source Wake-Word-System (MIT), nutzt
ONNX-Runtime und hat "hey_jarvis" als vortrainiertes Model eingebaut —
Null API-Key nötig.

Input-Format: 16 kHz mono int16 PCM, Frame-Größe muss 1280 Samples (80 ms)
sein. Wir pufern Mic-Chunks rein und splitten auf Frame-Grenze.

Output: Continuous Score [0, 1] pro Keyword — wir filtern auf
`activation_threshold` und debouncen (kein Re-Trigger innerhalb Cooldown).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator

import numpy as np

from jarvis.core.protocols import AudioChunk

log = logging.getLogger("jarvis.wake")


OWW_SAMPLE_RATE = 16_000
OWW_FRAME_SAMPLES = 1280  # openWakeWord erwartet genau diese Frame-Länge

# After the pipeline's STT prefix-verifier REJECTS a candidate, the detector
# stays deaf only this long (not the full cooldown). Short enough that a real
# "Hey Jarvis" spoken right after a false trigger gets through, long enough that
# continuous jarvis-like background audio cannot spin a reject->retrigger
# busy-loop of STT calls. See ``note_rejected_candidate``.
_REJECT_REFRACTORY_S = 0.8

# How often the always-on detector emits a cumulative-counter heartbeat at INFO
# (activation attempts, suppression reasons, loudest confidence this session).
# This is the user-facing "why didn't my wake word fire?" instrument: a live
# log line like "frames=… max_score=0.12 … triggers=0 (threshold 0.15)" shows
# at a glance that audio IS arriving and being scored, just below the bar — and
# a frames-stuck-at-N line makes a genuinely dead detector obvious.
_STATS_LOG_INTERVAL_S = 10.0

# Production wake threshold for this project's quiet-mic hardware, wired by
# jarvis/ui/desktop_app.py. Empirically derived from the 2026-05-24 idle-
# telemetry log (data/jarvis_desktop.log): ambient speech and bare "Hallo"
# false-fires land in the 0.05-0.11 score band, while genuine "Hey Jarvis"
# utterances peak at 0.15-0.23. 0.15 sits clearly above the ambient ceiling so
# the fast OWW path only fires on unambiguous wakes; the precise
# RollingWhisperWake pattern ("hey/hi/hallo"+"jarv") still catches quieter
# genuine wakes and can never match bare "Hallo".
#
# Threshold-pendulum history (each entry over- or under-corrected the previous):
#   0.40 -> 0.15 -> 0.10 -> 0.06 (over-correction: orb popped on every word)
#   -> 0.15 (this value, data-driven).
# DO NOT lower below the 0.10 floor guarded by
# tests/unit/speech/test_wake_threshold.py — that reintroduces the BUG-009
# over-correction where the orb pops up on ambient speech.
PRODUCTION_WAKE_THRESHOLD = 0.15


# Threshold + hysteresis strategy (single, documented contract):
#   * FIRE bar — a frame scores >= ``activation_threshold`` (default
#     ``PRODUCTION_WAKE_THRESHOLD``). ``WakeGainNormalizer`` lifts a quiet wake
#     into this band so a normal-volume utterance crosses it without shouting.
#   * DEBOUNCE — after a real trigger the detector stays quiet for ``cooldown_s``
#     so ONE spoken wake word yields exactly once (no machine-gun re-fire).
#   * REJECT REFRACTORY — when the pipeline's STT prefix-verify rejects a
#     candidate (``note_rejected_candidate``), the long debounce is cut to a
#     short ``_REJECT_REFRACTORY_S`` so a genuine wake spoken right after a false
#     positive still gets through, while continuous jarvis-like background audio
#     cannot spin a reject->retrigger busy-loop.
# There is no separate "release" threshold: the cooldown IS the hysteresis that
# keeps a sustained near-threshold score from chattering, and the BUG-009 floor
# (>= 0.10, pinned by tests/unit/speech/test_wake_threshold.py) keeps the FIRE
# bar above the ambient false-fire band.


class WakeGainNormalizer:
    """Amplify-only, noise-gated, capped streaming AGC for the OWW wake path.

    Root cause of "the wake word only triggers when I shout" (2026-06-28): in the
    default lightweight config the fast openWakeWord detector is the SOLE wake
    path (the ``RollingWhisperWake`` low-volume backstop is a power-user opt-in
    behind ``cfg.trigger.heavy_local_whisper``), and it fed RAW int16 frames into
    the neural model. openWakeWord's activation score scales with input level, so
    on this project's documented quiet-mic hardware (normal-speech rms ~0.01-0.02)
    a genuine "Hey Jarvis" peaks at ~0.10-0.14 — just *below* the pinned 0.15
    threshold. Only shouting lifted the level over the bar.

    The threshold cannot move (``test_wake_threshold`` pins the BUG-009 floor), so
    this brings the SAME peak normalization the ``RollingWhisperWake`` backstop
    already uses to the default OWW path. Per-frame behaviour:

    * the gain is derived from a rolling peak over the last ``window_s`` of audio
      (not the single frame) so it stays stable across the wake phrase and the
      intra-phrase envelope the model relies on is preserved;
    * it is AMPLIFY-ONLY — an already-loud wake is never turned down, so the
      genuine-peak band the threshold was calibrated on never regresses;
    * it is gated by ``noise_floor_peak`` — digital silence / idle hiss below the
      floor is returned UNCHANGED so the AGC can never manufacture an ambient
      false-fire band (the AGC-level analogue of the BUG-009 guard);
    * the gain is capped at ``max_gain_db`` so a near-silent floor cannot be blown
      up to full scale.

    Pure mechanism — no I/O, no model. ``reset()`` clears the rolling envelope so
    a stale loud burst does not suppress the gain on the next quiet utterance.
    """

    def __init__(
        self,
        target_peak_dbfs: float = -3.0,
        max_gain_db: float = 20.0,
        noise_floor_peak: float = 0.02,
        window_s: float = 1.5,
        sample_rate: int = OWW_SAMPLE_RATE,
        frame_samples: int = OWW_FRAME_SAMPLES,
    ) -> None:
        self._target_peak = float(10.0 ** (target_peak_dbfs / 20.0))  # -3 dBFS ≈ 0.707
        self._max_gain = float(10.0 ** (max_gain_db / 20.0))          # 20 dB = 10x
        self._noise_floor = float(noise_floor_peak)
        frame_s = frame_samples / sample_rate                          # 80 ms
        self._window_frames = max(1, int(round(window_s / frame_s)))
        self._recent_peaks: deque[float] = deque(maxlen=self._window_frames)

    def reset(self) -> None:
        """Forget the rolling envelope (called on detector stop / re-arm)."""
        self._recent_peaks.clear()

    def process(self, frame: np.ndarray) -> np.ndarray:
        """Return ``frame`` (int16) amplified toward the target peak, or
        unchanged when below the noise floor / already loud enough."""
        f32 = frame.astype(np.float32) / 32768.0
        frame_peak = float(np.max(np.abs(f32))) if f32.size else 0.0
        self._recent_peaks.append(frame_peak)
        rolling_peak = max(self._recent_peaks) if self._recent_peaks else frame_peak
        if rolling_peak < self._noise_floor:
            return frame  # silence / sub-floor hiss — leave it alone (gain 1.0)
        gain = min(self._target_peak / rolling_peak, self._max_gain)
        if gain <= 1.0:
            return frame  # already loud enough — amplify-only, never attenuate
        boosted = np.clip(f32 * gain, -1.0, 1.0)
        return (boosted * 32767.0).astype(np.int16)


class OpenWakeWordProvider:
    """Wake-Word-Detector — strukturell `WakeWordProvider`-kompatibel.

    Standardmäßig lauscht er auf "hey_jarvis" — das vortrainierte Modell
    reagiert aus meinem Test heraus sowohl auf "Hey Jarvis" als auch auf
    "Jarvis" allein, wenn der Threshold niedrig genug ist (0.5 tauglich).
    """

    name = "openwakeword"
    supported_keywords = ("hey_jarvis",)  # openWakeWord built-in Model

    def __init__(
        self,
        keywords: tuple[str, ...] = ("hey_jarvis",),
        # The FIRE bar. Defaults to the documented, data-driven
        # PRODUCTION_WAKE_THRESHOLD so omitting it gives the SAME behaviour the
        # desktop app wires explicitly — one consistent threshold, not a quieter
        # ad-hoc 0.10 that drifts the detector into the ambient false-fire band.
        # BUG-009: real-mic peaks for "Hey Jarvis" land at 0.10–0.20 on this
        # hardware (not the textbook 0.35-0.65); WakeGainNormalizer lifts a quiet
        # wake into that band. The 0.10 floor is pinned by test_wake_threshold.
        activation_threshold: float = PRODUCTION_WAKE_THRESHOLD,
        cooldown_s: float = 2.0,
        inference_framework: str = "onnx",
        # Diagnose-Log: alles >= diesem Wert loggen. 0.05 ist bewusst niedrig,
        # damit beim Testen Scores überhaupt sichtbar sind (sonst silent wenn
        # User zu leise spricht oder Model deutsche Aussprache nicht gut trifft).
        score_log_threshold: float = 0.05,
        # Explicit ONNX wake-model path. When set (custom-wake-word feature: a
        # pretrained alexa/mycroft/rhasspy from the openWakeWord package, or a
        # user-supplied custom .onnx) it overrides the bundled hey_jarvis model.
        # The shared melspec/embedding backbones are still reused from the
        # in-repo bundle so any model loads offline. None = bundled hey_jarvis.
        model_path: str | None = None,
        # Volume-robust wake (root cause of "only triggers when shouted",
        # 2026-06-28): openWakeWord's score is amplitude-dependent, so on a quiet
        # mic a normal-volume "Hey Jarvis" under-scores against the threshold and
        # only a shouted utterance crosses it. When ``gain_normalization`` is True
        # (default) each frame is routed through ``WakeGainNormalizer`` — an
        # amplify-only, noise-gated, capped streaming AGC — BEFORE
        # ``model.predict()`` so the score reflects the wake *pattern*, not the
        # volume. Set False to restore the legacy raw-PCM behaviour (escape hatch).
        gain_normalization: bool = True,
    ) -> None:
        self._keywords = keywords
        self._threshold = activation_threshold
        self._cooldown_s = cooldown_s
        self._inference_framework = inference_framework
        self._score_log_threshold = score_log_threshold
        self._model_path = model_path
        self._model = None  # lazy
        self._last_trigger_ns: int = 0
        self._residual = np.empty(0, dtype=np.int16)
        # Volume-robust wake: lift quiet input toward a target peak before
        # scoring (None = escape hatch, raw PCM straight to the model).
        self._gain = WakeGainNormalizer() if gain_normalization else None
        # Per-session debug counters (reset on each detect() entry). They make
        # "the wake word never fires / sometimes stops entirely" diagnosable:
        # the user can see frames ARE arriving and being scored, how loud the
        # loudest was, and exactly why each near-hit was dropped (below the FIRE
        # bar vs swallowed by the cooldown debounce). Exposed via ``stats()``.
        self._frames_seen = 0
        self._max_score = 0.0
        self._last_score = 0.0
        self._attempts_above_threshold = 0
        self._suppressed_below_threshold = 0
        self._suppressed_cooldown = 0
        self._triggers = 0
        self._stats_window_start_ns = 0

    def _reset_session_stats(self) -> None:
        """Zero the per-session counters and re-arm the heartbeat window."""
        self._frames_seen = 0
        self._max_score = 0.0
        self._last_score = 0.0
        self._attempts_above_threshold = 0
        self._suppressed_below_threshold = 0
        self._suppressed_cooldown = 0
        self._triggers = 0
        self._stats_window_start_ns = time.time_ns()

    def stats(self) -> dict[str, float]:
        """Snapshot of the current listen session's activation counters.

        Keys: ``frames_seen``, ``max_score``, ``last_score``,
        ``attempts_above_threshold``, ``suppressed_below_threshold``,
        ``suppressed_cooldown``, ``triggers``, ``threshold``. Consumed by the
        heartbeat log and the wake diagnostics surface.
        """
        return {
            "frames_seen": self._frames_seen,
            "max_score": self._max_score,
            "last_score": self._last_score,
            "attempts_above_threshold": self._attempts_above_threshold,
            "suppressed_below_threshold": self._suppressed_below_threshold,
            "suppressed_cooldown": self._suppressed_cooldown,
            "triggers": self._triggers,
            "threshold": self._threshold,
        }

    def _maybe_log_stats_heartbeat(self) -> None:
        """Emit the cumulative counter snapshot at INFO every interval."""
        now_ns = time.time_ns()
        if now_ns - self._stats_window_start_ns < _STATS_LOG_INTERVAL_S * 1e9:
            return
        log.info(
            "wake stats: frames=%d max_score=%.3f attempts>=thr=%d "
            "suppressed[below_thr=%d cooldown=%d] triggers=%d (threshold %.2f)",
            self._frames_seen,
            self._max_score,
            self._attempts_above_threshold,
            self._suppressed_below_threshold,
            self._suppressed_cooldown,
            self._triggers,
            self._threshold,
        )
        self._stats_window_start_ns = now_ns

    def _model_kwargs(self) -> dict:
        """Build the openWakeWord ``Model(...)`` kwargs.

        Prefer the ONNX models bundled in-repo (``jarvis/assets/wakeword/``):
        passing explicit local paths keeps the wake path offline on first boot
        and avoids the package-cache auto-download. When the bundle is absent
        (e.g. a partial checkout), fall back to built-in keyword names, which
        triggers openWakeWord's own auto-download.
        """
        import jarvis.assets

        bundled = jarvis.assets.bundled_wakeword_models()

        # Custom-wake-word path: an explicit model overrides the bundled
        # hey_jarvis wakeword, but the shared melspec/embedding backbones are
        # reused from the bundle (they are model-agnostic) so any pretrained or
        # custom model still loads offline. If the bundle is absent, hand the
        # bare path to openWakeWord (it auto-resolves backbones from its own
        # package resources).
        if self._model_path:
            kwargs: dict = {
                "wakeword_models": [self._model_path],
                "inference_framework": "onnx",
            }
            if bundled is not None:
                kwargs["melspec_model_path"] = str(bundled["melspec"])
                kwargs["embedding_model_path"] = str(bundled["embedding"])
            return kwargs

        if bundled is not None:
            return {
                "wakeword_models": [str(bundled["wakeword"])],
                "melspec_model_path": str(bundled["melspec"]),
                "embedding_model_path": str(bundled["embedding"]),
                "inference_framework": "onnx",
            }
        return {
            "wakeword_models": list(self._keywords),
            "inference_framework": self._inference_framework,
        }

    def _canonical_keyword(self, raw: str) -> str:
        """Map a raw openWakeWord model key back to the configured keyword.

        Loading the bundled ``hey_jarvis_v0.1.onnx`` makes openWakeWord report
        the score under the file stem ``hey_jarvis_v0.1``. Downstream code and
        ``supported_keywords`` use the canonical ``hey_jarvis``; normalise so a
        bundled load and a built-in load are indistinguishable to consumers.
        """
        for kw in self._keywords:
            if raw == kw or raw.startswith(f"{kw}_"):
                return kw
        return raw

    def _ensure_model(self) -> None:
        if self._model is None:
            from openwakeword.model import Model
            # wakeword_models=[...] akzeptiert entweder Built-in-Namen oder
            # Pfade zu .onnx / .tflite Dateien. Wir bevorzugen die gebündelten
            # lokalen ONNX-Pfade (siehe _model_kwargs) — kein Runtime-Download,
            # offline-fähig beim ersten Start.
            self._model = Model(**self._model_kwargs())

    async def start(self) -> None:
        """Pre-load Model — spart Latenz beim ersten Audio-Frame."""
        await asyncio.to_thread(self._ensure_model)

    async def stop(self) -> None:
        self._model = None
        self._residual = np.empty(0, dtype=np.int16)
        if self._gain is not None:
            self._gain.reset()

    def _cooldown_ok(self, now_ns: int) -> bool:
        """True if the debounce window since the last yielded trigger elapsed.

        The cooldown debounces ONE spoken wake word into a single trigger; it
        is NOT meant to deafen the detector after a candidate the pipeline
        later rejects (see ``note_rejected_candidate``).
        """
        return now_ns - self._last_trigger_ns >= self._cooldown_s * 1e9

    def note_rejected_candidate(self, now_ns: int | None = None) -> None:
        """The pipeline's STT prefix-verifier rejected the last yielded
        candidate as a false positive (bare "Jarvis", background speech).

        Shorten the debounce so a genuine "Hey Jarvis" spoken ~1 s later still
        triggers (instead of being swallowed for the full ``cooldown_s``), while
        leaving a SHORT refractory (``_REJECT_REFRACTORY_S``) so continuous
        jarvis-like background audio cannot spin a reject->retrigger busy-loop of
        STT verification calls. ``now_ns`` is injectable for deterministic tests.
        """
        now = time.time_ns() if now_ns is None else now_ns
        held = max(0.0, self._cooldown_s - _REJECT_REFRACTORY_S)
        self._last_trigger_ns = now - int(held * 1e9)

    async def detect(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[str]:
        """Konsumiert Audio-Chunks, yielded Keyword-Namen bei Detection.

        Das ist die Convenience-API — intern nutzen wir `stream()` für
        rohe Confidence-Werte (Protocol-Anforderung).
        """
        self._ensure_model()
        assert self._model is not None

        # Per-session reset (the "no dead state blocks waking" guard): start each
        # listen from clean audio + counter state so a stale loud gain envelope
        # or leftover residual from the PREVIOUS interaction can never
        # under-amplify (and thus silently swallow) the next quiet wake. The
        # debounce stamp (_last_trigger_ns) is deliberately NOT reset — the
        # cooldown must persist across re-arm so a just-fired wake does not
        # immediately re-fire.
        self._residual = np.empty(0, dtype=np.int16)
        if self._gain is not None:
            self._gain.reset()
        self._reset_session_stats()

        async for chunk in chunks:
            # Int16-View auf die Bytes — openWakeWord erwartet int16 arrays
            int16 = np.frombuffer(chunk.pcm, dtype=np.int16)
            buf = np.concatenate([self._residual, int16])

            # In 1280-Sample-Frames splitten, Rest aufheben
            n_full = len(buf) // OWW_FRAME_SAMPLES
            if n_full == 0:
                self._residual = buf
                continue
            frames = buf[: n_full * OWW_FRAME_SAMPLES].reshape(n_full, OWW_FRAME_SAMPLES)
            self._residual = buf[n_full * OWW_FRAME_SAMPLES:]

            for frame in frames:
                # Volume-robust wake: lift a quiet frame toward the target peak
                # so a normal-volume "Hey Jarvis" reaches the score band the
                # threshold expects (no shouting). Amplify-only + noise-floor gated.
                norm_frame = self._gain.process(frame) if self._gain is not None else frame
                # predict() gibt dict zurück: {"hey_jarvis": score_float, ...}
                scores = await asyncio.to_thread(self._model.predict, norm_frame)
                self._frames_seen += 1
                frame_max = max(scores.values()) if scores else 0.0
                self._last_score = frame_max
                if frame_max > self._max_score:
                    self._max_score = frame_max
                for keyword, score in scores.items():
                    # Live-Debugging: alles über `score_log_threshold` loggen,
                    # damit der User beim Testen sieht wie nah er am Hit dran ist.
                    if score >= self._score_log_threshold:
                        log.info("wake score  %s = %.3f  (threshold %.2f)",
                                 keyword, score, self._threshold)
                    if score < self._threshold:
                        self._suppressed_below_threshold += 1
                        continue
                    # Cleared the FIRE bar — record the attempt before the
                    # debounce decides whether it actually yields.
                    self._attempts_above_threshold += 1
                    now_ns = time.time_ns()
                    if not self._cooldown_ok(now_ns):
                        self._suppressed_cooldown += 1
                        log.info(
                            "wake suppressed: cooldown — %s score=%.3f >= %.2f "
                            "(%.1fs since last trigger, need %.1fs)",
                            keyword, score, self._threshold,
                            (now_ns - self._last_trigger_ns) / 1e9,
                            self._cooldown_s,
                        )
                        continue
                    self._last_trigger_ns = now_ns
                    self._triggers += 1
                    log.info(
                        "wake ACCEPTED: %s score=%.3f >= threshold %.2f",
                        keyword, score, self._threshold,
                    )
                    yield self._canonical_keyword(keyword)
                    break  # one yield per frame (multi-keyword models)
                self._maybe_log_stats_heartbeat()

    async def stream(self) -> AsyncIterator[float]:
        """Protocol-Pflicht: Confidence-Stream. Nicht die primäre API hier."""
        # Placeholder — wir nutzen `detect()` intern. Für Protocol-Konformität
        # müsste der Consumer sein eigenes Audio einspeisen.
        if False:
            yield 0.0
        return
