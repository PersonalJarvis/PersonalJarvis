"""openWakeWord plugin — detects "Hey Jarvis" / "Jarvis" in the audio stream.

openWakeWord is a free, open-source wake-word system (MIT), uses the
ONNX runtime, and ships "hey_jarvis" as a built-in pretrained model —
zero API key needed.

Input format: 16 kHz mono int16 PCM, frame size must be 1280 samples (80 ms).
We buffer mic chunks and split on the frame boundary.

Output: continuous score [0, 1] per keyword — we filter on
`activation_threshold` and debounce (no re-trigger within the cooldown).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

import numpy as np

from jarvis.audio.wake_normalizer import AdaptiveWakeNormalizer
from jarvis.core.protocols import AudioChunk

log = logging.getLogger("jarvis.wake")


OWW_SAMPLE_RATE = 16_000
OWW_FRAME_SAMPLES = 1280  # openWakeWord expects exactly this frame length

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
    """Amplify-only, adaptive-floor, capped streaming AGC for the OWW wake path.

    Thin adapter over the shared :class:`jarvis.audio.wake_normalizer.
    AdaptiveWakeNormalizer` so the default openWakeWord path and the
    ``RollingWhisperWake`` backstop share ONE input-normalization mechanism
    (mission 2026-06-30). It exists as its own name for the OWW-specific defaults
    (frame size 1280) and the existing call sites/tests.

    Root cause of "the wake word only triggers when I shout" (2026-06-28): the
    fast openWakeWord detector fed RAW int16 frames into the neural model, whose
    activation score scales with input level, so on a quiet mic a genuine
    "Hey Jarvis" under-scored the pinned 0.15 threshold and only a shout crossed
    it. The 2026-06-28 fix added peak normalization but gated it on a FIXED
    ``noise_floor_peak=0.02`` — a genuinely quiet wake between that floor and true
    silence still got zero gain. This now uses an ADAPTIVE floor (mission
    2026-06-30): on a quiet mic the floor settles to the real ambient level, so a
    quiet wake rises above it and is amplified, while flat silence / steady
    sub-floor hiss is still left unchanged. The 0.15 threshold and the
    amplify-only + sub-floor guards are unchanged, so quiet wakes are lifted
    without widening the ambient false-fire band.
    """

    def __init__(
        self,
        target_peak_dbfs: float = -3.0,
        max_gain_db: float = 30.0,
        # Now the ADAPTIVE floor's STARTING value (was a fixed hard gate). Lower
        # than the legacy 0.02 so a quiet-but-real wake is above the fresh-session
        # speech threshold; it adapts further down on a quiet mic.
        noise_floor_peak: float = 0.006,
        window_s: float = 1.5,
        sample_rate: int = OWW_SAMPLE_RATE,
        frame_samples: int = OWW_FRAME_SAMPLES,
    ) -> None:
        self._norm = AdaptiveWakeNormalizer(
            target_peak_dbfs=target_peak_dbfs,
            max_gain_db=max_gain_db,
            floor_start=noise_floor_peak,
            window_s=window_s,
            sample_rate=sample_rate,
            frame_samples=frame_samples,
        )

    def reset(self) -> None:
        """Forget the rolling envelope (called on detector stop / re-arm)."""
        self._norm.reset()

    def process(self, frame: np.ndarray) -> np.ndarray:
        """Return ``frame`` (int16) amplified toward the target peak, or
        unchanged when below the adaptive floor / already loud enough."""
        return self._norm.process(frame)


class OpenWakeWordProvider:
    """Wake-word detector — structurally compatible with `WakeWordProvider`.

    By default it listens for "hey_jarvis" — in my testing the pretrained
    model reacts to both "Hey Jarvis" and "Jarvis" alone, as long as the
    threshold is low enough (0.5 works).
    """

    name = "openwakeword"
    supported_keywords = ("hey_jarvis",)  # openWakeWord built-in model

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
        # Diagnostic log: log everything >= this value. 0.05 is deliberately low
        # so scores are visible at all during testing (otherwise silent when
        # the user speaks too quietly, or the model doesn't handle German
        # pronunciation well).
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
        # Set once if the openWakeWord runtime cannot be imported, so the
        # detector degrades to a logged no-op instead of crashing the speech
        # pipeline. openwakeword + onnxruntime are BASE deps (2026-07-04), so
        # this should never trip on a normal install — it is a safety net for a
        # broken/partial environment.
        self._runtime_unavailable = False
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
        if self._model is not None or self._runtime_unavailable:
            return
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            # openwakeword + onnxruntime are BASE deps, so this only happens on a
            # broken/partial install. Degrade to a no-op wake (the pipeline keeps
            # running; the user can still talk via the app/push-to-talk) instead
            # of an uncaught ImportError that would take the whole speech pipeline
            # down. Actionable, English, one line.
            self._runtime_unavailable = True
            log.error(
                "Wake word disabled: the openWakeWord runtime is not importable "
                "(%s). Reinstall the base dependencies (`pip install -e .`) to "
                "restore the always-on wake word.",
                exc,
            )
            return
        # wakeword_models=[...] accepts either built-in names or paths to
        # .onnx / .tflite files. We prefer the bundled local ONNX paths
        # (see _model_kwargs) — no runtime download, offline-capable on
        # first boot.
        self._model = Model(**self._model_kwargs())

    def _warmup_model(self) -> None:
        """Run ONE throwaway inference so the first real wake frame is not cold.

        ``_ensure_model`` only LOADS the ONNX graph; the first ``predict`` still
        pays the onnxruntime graph-init / melspec+embedding warm cost, and that
        used to land on the user's first "Hey Jarvis" (a swallowed wake + a
        visible delay before the bar — mission 2026-06-30 "~0.5 s delay"). Priming
        it here moves the cost off the wake path. Fail-closed: a warm-up error
        (e.g. a partially-loaded model) degrades to a no-op, never breaks boot.
        """
        model = self._model
        if model is None:
            return
        try:
            dummy = np.zeros(OWW_FRAME_SAMPLES, dtype=np.int16)
            model.predict(dummy)
        except Exception as exc:  # noqa: BLE001 — warm-up must never break boot
            log.debug("OWW warm-up inference skipped: %s", exc)

    async def start(self) -> None:
        """Pre-load AND warm the model — saves cold-start latency on the first frame."""
        await asyncio.to_thread(self._ensure_model)
        await asyncio.to_thread(self._warmup_model)

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
        """Consumes audio chunks, yields keyword names on detection.

        This is the convenience API — internally we use `stream()` for
        raw confidence values (protocol requirement).
        """
        self._ensure_model()
        if self._model is None:
            # Runtime unavailable (already logged in _ensure_model). Degrade to a
            # no-op detector: end the stream cleanly so the speech pipeline keeps
            # running instead of dying on an uncaught error.
            return

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
            # Int16 view over the bytes — openWakeWord expects int16 arrays
            int16 = np.frombuffer(chunk.pcm, dtype=np.int16)
            buf = np.concatenate([self._residual, int16])

            # Split into 1280-sample frames, keep the remainder
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
                # predict() returns a dict: {"hey_jarvis": score_float, ...}
                scores = await asyncio.to_thread(self._model.predict, norm_frame)
                self._frames_seen += 1
                frame_max = max(scores.values()) if scores else 0.0
                self._last_score = frame_max
                if frame_max > self._max_score:
                    self._max_score = frame_max
                for keyword, score in scores.items():
                    # Live debugging: log everything above `score_log_threshold`,
                    # so during testing the user can see how close they are to a hit.
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
        """Protocol requirement: confidence stream. Not the primary API here."""
        # Placeholder — we use `detect()` internally. For protocol conformance
        # the consumer would need to feed in its own audio.
        if False:
            yield 0.0
        return
