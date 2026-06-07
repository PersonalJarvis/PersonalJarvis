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
        # BUG-009: real-mic peaks for "Hey Jarvis" land at 0.10–0.20 on this
        # hardware, not the textbook 0.35-0.65.
        activation_threshold: float = 0.10,
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
                # predict() gibt dict zurück: {"hey_jarvis": score_float, ...}
                scores = await asyncio.to_thread(self._model.predict, frame)
                for keyword, score in scores.items():
                    # Live-Debugging: alles über `score_log_threshold` loggen,
                    # damit der User beim Testen sieht wie nah er am Hit dran ist.
                    if score >= self._score_log_threshold:
                        log.info("wake score  %s = %.3f  (threshold %.2f)",
                                 keyword, score, self._threshold)
                    if score < self._threshold:
                        continue
                    now_ns = time.time_ns()
                    if not self._cooldown_ok(now_ns):
                        continue
                    self._last_trigger_ns = now_ns
                    yield self._canonical_keyword(keyword)

    async def stream(self) -> AsyncIterator[float]:
        """Protocol-Pflicht: Confidence-Stream. Nicht die primäre API hier."""
        # Placeholder — wir nutzen `detect()` intern. Für Protocol-Konformität
        # müsste der Consumer sein eigenes Audio einspeisen.
        if False:
            yield 0.0
        return
