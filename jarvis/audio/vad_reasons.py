"""Canonical VAD endpoint reasons — single source of truth.

Both the VAD producer (:mod:`jarvis.audio.vad`) and the speech-pipeline
consumer (:mod:`jarvis.speech.pipeline`) must agree on these strings.
Defining them once prevents the multi-layer enum-drift bug class (see
``docs/anti-drift-three-layer.md``, anti-pattern AP-4): a reason emitted by
the VAD but unknown to the consumer would silently break turn finalization.

Semantics:

* ``silence`` / ``stt_stable`` — the user actually stopped talking. The
  yielded PCM is a complete turn → finalize (transcribe + brain).
* ``max_utterance`` — the VAD hit its length cap while the user was *still
  talking*. The yielded PCM is a FRAGMENT of an ongoing utterance, not a
  finished turn → the consumer must accumulate and keep listening.
* ``false_start`` — endpoint fired but too little real speech was present;
  no PCM is yielded at all (purely informational for callbacks).
"""
from __future__ import annotations

VAD_REASON_SILENCE = "silence"
VAD_REASON_MAX_UTTERANCE = "max_utterance"
VAD_REASON_STT_STABLE = "stt_stable"
VAD_REASON_FALSE_START = "false_start"

#: Reasons that mean "the VAD cut a still-ongoing utterance short". The
#: pipeline buffers these fragments and merges them with the next segment
#: instead of running an independent (truncated) brain turn.
FORCED_CUT_REASONS = frozenset({VAD_REASON_MAX_UTTERANCE})

#: Reasons that mean "the user finished" → finalize the (possibly merged) turn.
NATURAL_END_REASONS = frozenset({VAD_REASON_SILENCE, VAD_REASON_STT_STABLE})
