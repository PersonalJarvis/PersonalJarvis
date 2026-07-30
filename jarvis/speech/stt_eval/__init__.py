"""Offline, provider-neutral quality evaluation for speech recognition.

The package contains no corpus and performs no network work at import time.
Users supply consented, labelled audio through a JSONL manifest; provider calls
happen only when the explicit CLI is run.
"""

from jarvis.speech.stt_eval.corpus import STTEvalItem, load_corpus
from jarvis.speech.stt_eval.harness import (
    ContenderReport,
    EvaluationReport,
    Recognition,
    evaluate_contender,
)
from jarvis.speech.stt_eval.metrics import repeatability_error_rate, switch_error_rate

__all__ = [
    "ContenderReport",
    "EvaluationReport",
    "Recognition",
    "STTEvalItem",
    "evaluate_contender",
    "load_corpus",
    "repeatability_error_rate",
    "switch_error_rate",
]
