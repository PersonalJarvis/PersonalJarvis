"""Objective, offline TTS evaluation suite.

Scores a TTS provider on the four metrics the curation gates on — round-trip
ASR error (WER), naturalness MOS, voice-drift, and latency (TTFA/RTF) — over a
hard de/en/es corpus, and produces a pass/fail verdict against acceptance
thresholds. Runs OFF the voice hot path (a CLI / CI tool, never in a live turn).

Design: docs/superpowers/specs/2026-07-07-tts-quality-curation-design.md §3.6.

Cross-platform: the DNSMOS (onnxruntime) naturalness floor is torch-free and
runs on a headless VPS; the round-trip-ASR WER backend uses faster-whisper
(the `[tts-eval]` extra). Every metric backend is lazy-imported and degrades to
``None`` (logged) when its dependency/model is absent — the harness never
crashes, it just reports fewer metrics.
"""
from __future__ import annotations

from jarvis.speech.tts_eval.corpus import HARD_CORPUS, EvalItem, items_for_language
from jarvis.speech.tts_eval.harness import (
    EvalReport,
    ItemReport,
    MetricResult,
    Metrics,
    SynthResult,
    Thresholds,
    evaluate,
    gate,
)

__all__ = [
    "HARD_CORPUS",
    "EvalItem",
    "EvalReport",
    "ItemReport",
    "MetricResult",
    "Metrics",
    "SynthResult",
    "Thresholds",
    "evaluate",
    "gate",
    "items_for_language",
]
