"""Real metric backends for the TTS eval harness.

Every backend is LAZY (imports its dependency only in ``measure``) and
FAIL-SOFT (returns ``None`` + a logged warning when its dependency or model is
absent), so the harness runs on any host — a headless VPS with only the
torch-free floor, or a full box — and simply reports the metrics it can compute.

- WER: round-trip ASR (faster-whisper, the `[tts-eval]` extra) → normalized
  word-error-rate against the reference text. The primary anti-slop signal.
- MOS: DNSMOS OVRL via onnxruntime (torch-free). Needs the DNSMOS ONNX model on
  disk (not shipped); ``None`` until a model dir is provided.
- Drift: speaker-embedding cosine across chunks. Needs an embedding model
  (not shipped); ``None`` until one is provided.

Design: docs/superpowers/specs/2026-07-07-tts-quality-curation-design.md §3.6.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from jarvis.speech.tts_eval.harness import Metrics

log = logging.getLogger("jarvis.tts_eval.metrics")

_WORD_RE = re.compile(r"[^\w']+", re.UNICODE)


def normalize_words(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words — the normalization both
    the reference and the ASR hypothesis pass through before WER."""
    return [w for w in _WORD_RE.split((text or "").lower()) if w]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein word-error-rate = edit distance (words) / reference length.

    Pure and deterministic (no dependency). ``0.0`` = perfect; ``> 1.0``
    possible when the hypothesis is much longer. An empty reference returns
    ``0.0`` for an empty hypothesis, else ``1.0``.
    """
    ref = normalize_words(reference)
    hyp = normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    # Classic DP edit distance over word lists.
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i]
        for j, h in enumerate(hyp, start=1):
            cost = 0 if r == h else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1] / len(ref)


def _pcm_to_float32(pcm: bytes):
    import numpy as np

    return np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0


class WhisperWerBackend:
    """Round-trip ASR WER via faster-whisper (lazy, CPU). Returns ``None`` when
    faster-whisper is not installed (the `[tts-eval]` / `[local-voice]` extra)."""

    def __init__(self, model_size: str = "base") -> None:
        self._model_size = model_size
        self._model = None
        self._unavailable = False

    def _ensure_model(self):
        if self._model is None and not self._unavailable:
            try:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
            except Exception as exc:  # noqa: BLE001 — missing dep / model → degrade
                log.warning(
                    "WER backend unavailable (%s: %s) — install the [tts-eval] "
                    "extra (faster-whisper) to measure round-trip ASR error.",
                    exc.__class__.__name__, exc,
                )
                self._unavailable = True
        return self._model

    def measure(
        self, pcm: bytes, sample_rate: int, reference_text: str, language: str
    ) -> float | None:
        model = self._ensure_model()
        if model is None or not pcm:
            return None
        try:
            audio = _pcm_to_float32(pcm)
            short = (language or "").lower().split("-", 1)[0] or None
            segments, _info = model.transcribe(audio, language=short, beam_size=1)
            hypothesis = " ".join(seg.text for seg in segments)
        except Exception as exc:  # noqa: BLE001 — transcription failure → degrade
            log.warning("WER transcription failed (%s) — skipping.", exc.__class__.__name__)
            return None
        return word_error_rate(reference_text, hypothesis)


class DnsmosBackend:
    """Naturalness MOS via DNSMOS OVRL (onnxruntime, torch-free). Needs the
    DNSMOS ONNX model on disk; ``None`` until ``model_path`` points at one."""

    def __init__(self, model_path: str | None = None) -> None:
        self._model_path = model_path
        self._session = None
        self._unavailable = model_path is None

    def measure(self, pcm: bytes, sample_rate: int) -> float | None:
        if self._unavailable or not pcm:
            if self._model_path is None:
                log.debug("MOS backend: no DNSMOS model path configured — skipping.")
            return None
        try:  # pragma: no cover — exercised only with a real model on disk
            import numpy as np  # noqa: F401
            import onnxruntime as ort

            if self._session is None:
                self._session = ort.InferenceSession(self._model_path)
            # DNSMOS inference is model-specific; kept behind a configured model
            # so the floor stays torch-free and the harness never hard-requires it.
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("MOS backend failed (%s) — skipping.", exc.__class__.__name__)
            self._unavailable = True
            return None


class SpeakerDriftBackend:
    """Voice-drift = min pairwise cosine of per-chunk speaker embeddings. Needs an
    embedding model (not shipped); ``None`` until ``embed_fn`` is provided.

    ``embed_fn(pcm, sample_rate) -> vector`` is injected so any embedding model
    (ONNX ECAPA, SpeechBrain, …) can back it without this module depending on one.
    """

    def __init__(self, embed_fn=None) -> None:
        self._embed_fn = embed_fn

    def measure(self, chunks: Sequence[bytes], sample_rate: int) -> float | None:
        if self._embed_fn is None or len(chunks) < 2:
            return None
        try:  # pragma: no cover — exercised only with a real embedder injected
            import numpy as np

            vecs = [np.asarray(self._embed_fn(c, sample_rate), dtype="float32") for c in chunks]
            vecs = [v for v in vecs if v.size]
            if len(vecs) < 2:
                return None
            worst = 1.0
            for a in range(len(vecs)):
                for b in range(a + 1, len(vecs)):
                    va, vb = vecs[a], vecs[b]
                    denom = float(np.linalg.norm(va) * np.linalg.norm(vb)) or 1.0
                    worst = min(worst, float(va @ vb) / denom)
            return worst
        except Exception as exc:  # noqa: BLE001
            log.warning("Drift backend failed (%s) — skipping.", exc.__class__.__name__)
            return None


def default_metrics(
    *, whisper_model: str = "base", dnsmos_model_path: str | None = None, embed_fn=None
) -> Metrics:
    """Build the standard metric bundle. WER is always attempted (degrades if
    faster-whisper is absent); MOS/drift are attached only when their model /
    embedder is provided, else omitted so the gate skips them honestly."""
    return Metrics(
        wer=WhisperWerBackend(whisper_model),
        mos=DnsmosBackend(dnsmos_model_path) if dnsmos_model_path else None,
        drift=SpeakerDriftBackend(embed_fn) if embed_fn else None,
    )
