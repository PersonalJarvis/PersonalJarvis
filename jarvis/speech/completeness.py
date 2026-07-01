"""Utterance-completeness classifier — pre-processing in front of the main agent.

Decides whether a finalized STT transcript is a COMPLETE actionable instruction,
an INCOMPLETE (dangling) fragment, or an ABRUPT_ABORT (the user cancelled their
own dictation). The pipeline reacts by either dispatching to the brain
(COMPLETE) or staying in LISTENING with a short signal (INCOMPLETE / ABORT) —
never shipping a half-command to the brain.

Design: docs/superpowers/specs/2026-05-25-utterance-completeness-design.md

Bias is **"when in doubt, execute"**: the default verdict is COMPLETE, and only
high-precision rules raise INCOMPLETE / ABRUPT_ABORT. This keeps the fast
local-action path (``match_local_action``) and the meta-command gates
(``match_voice_command``) un-starved — a clear command like "Open Chrome" must
always pass through.

Standard-library only (``re``), like ``jarvis/speech/hangup.py`` — no
``sounddevice`` / heavy imports, so the telephony path
(``jarvis/telephony/session.py``) can import it as well. The reaction (earcon vs
spoken cue) is surface-specific and lives in the callers; this module is the
single shared *decision*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final


class Completeness(str, Enum):
    """Verdict label. ``str``-backed so it is cheap to log / put on an event
    payload. **Python-internal** — deliberately not a wire-format enum (no SQL /
    TS / UI mirror), see spec §8 (BUG-008 drift avoidance)."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    ABRUPT_ABORT = "abrupt_abort"


@dataclass(frozen=True, slots=True)
class CompletenessVerdict:
    """Result of :func:`classify_completeness`.

    ``reason`` names the rule that fired ("empty" / "abort" / "dangling" /
    "terminal" / "cut_off" / "default" / "error_fail_open") for logs + telemetry.
    """

    label: Completeness
    reason: str


# --- Rule 2: abrupt-abort phrases (explicit self-cancel) ------------------
# Anchored full-utterance match (optional leading interjection + optional
# trailing "jarvis"). Anchoring keeps precision high: "ich vergiss es nie" and
# "mach das doch nicht so laut" do NOT match because there is extra content  # i18n-allow
# outside the phrase. A bare "nein" / "no" is a valid answer, not an abort.
_ABORT_INTERJECTIONS: Final[str] = r"ach|oh|ne|nee|nein|ok|okay|also|ja|na|hm|ah"

_ABORT_PHRASES: Final[tuple[str, ...]] = (
    # German — longer variants first (anchoring makes order non-critical, but
    # keep it tidy)
    "lass mal gut sein",
    "lass es gut sein",
    "lass gut sein",
    "ist schon gut",  # i18n-allow
    "is schon gut",
    "schon gut",
    "nein doch nicht",  # i18n-allow
    "nee doch nicht",  # i18n-allow
    "ne doch nicht",  # i18n-allow
    "doch nicht",  # i18n-allow
    "nein egal",
    "nee egal",
    "ne egal",
    "ach egal",
    "na egal",
    "vergiss es",
    "vergiss das",
    "vergisses",
    "ach nichts",
    "schon nichts",
    # English
    "forget about it",
    "never mind",
    "nevermind",
    "forget it",
    "forget that",
    "scratch that",
    "no wait",
    "nvm",
)

_ABORT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:" + _ABORT_INTERJECTIONS + r")\s+)?"
    r"(?:" + "|".join(re.escape(p) for p in _ABORT_PHRASES) + r")"
    r"(?:\s+jarvis)?$"
)


# --- Rule 3: trailing dangling tokens -------------------------------------
# Language-agnostic on purpose: the sets are disjoint enough across DE/EN that a
# single combined set is robust even when STT mis-detects the language. The one
# real collision is "an" (EN article vs DE separable-verb particle in "mach das
# Licht an") — it is dropped entirely so the German particle wins. Prepositions
# and bare definite articles are excluded too (they collide with question tails
# and demonstrative pronouns); see spec §4.
_DANGLING: Final[frozenset[str]] = frozenset(
    {
        # DE conjunctions
        "und", "oder", "aber", "weil", "dass", "daß", "denn", "sondern", "sowie",  # i18n-allow
        # DE indefinite articles (almost always pre-nominal)
        "eine", "einen", "einem", "einer", "eines",  # i18n-allow
        # DE subordinators
        "wenn", "falls", "ob",
        # EN conjunctions
        "and", "or", "but", "because",
        # EN articles
        "the", "a",
        # EN preposition that is practically never a complete final token
        "to",
        # EN subordinators
        "if", "when",
    }
)

_TERMINAL_PUNCT: Final[tuple[str, ...]] = (".", "!", "?")
_STRIP_CHARS: Final[str] = ".,!?;:\"'»«…()-"


def _normalize_abort(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for abort matching."""
    lowered = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", lowered).strip()


def _last_token(text: str) -> str:
    parts = text.lower().split()
    if not parts:
        return ""
    return parts[-1].strip(_STRIP_CHARS)


def classify_completeness(
    text: str,
    *,
    lang: str = "de",
    endpoint_reason: str | None = None,
    stt_confidence: float | None = None,
    duration_ms: int | None = None,
) -> CompletenessVerdict:
    """Classify a finalized transcript. Pure, deterministic, no I/O.

    Parameters
    ----------
    text:
        The finalized STT transcript.
    lang:
        Language hint ("de" / "en"). Accepted for API stability; the v1 rules are
        language-agnostic (see ``_DANGLING``).
    endpoint_reason:
        VAD endpoint reason. ``"max_utterance"`` means the turn was chopped at the
        cap — a strong cut-off signal (rule 5, C-signal).
    stt_confidence, duration_ms:
        Reserved C-signals for a later tuning pass; accepted but unused in v1.

    Returns
    -------
    CompletenessVerdict
        Never raises — on any unexpected error it fails **open** to COMPLETE
        ("when in doubt, execute"; mirrors the AD-OE6 never-mute invariant).
    """
    try:
        raw = (text or "").strip()
        # Rule 1 — empty / whitespace-only (defensive; pipeline guards earlier)
        if not raw:
            return CompletenessVerdict(Completeness.INCOMPLETE, "empty")

        # Rule 2 — explicit abrupt-abort phrase
        if _ABORT_RE.match(_normalize_abort(raw)):
            return CompletenessVerdict(Completeness.ABRUPT_ABORT, "abort")

        # Rule 3 — trailing dangling function word / subordinator
        if _last_token(raw) in _DANGLING:
            return CompletenessVerdict(Completeness.INCOMPLETE, "dangling")

        # Rule 4 — explicit terminal punctuation closes the thought
        if raw.rstrip().endswith(_TERMINAL_PUNCT):
            return CompletenessVerdict(Completeness.COMPLETE, "terminal")

        # Rule 5 — C-signal: chopped at the max-utterance cap, no terminal punct
        if endpoint_reason == "max_utterance":
            return CompletenessVerdict(Completeness.INCOMPLETE, "cut_off")

        # Rule 6 — default (the "execute" bias)
        return CompletenessVerdict(Completeness.COMPLETE, "default")
    except Exception:  # noqa: BLE001 — fail-open: a classifier bug must never mute
        return CompletenessVerdict(Completeness.COMPLETE, "error_fail_open")


__all__ = [
    "Completeness",
    "CompletenessVerdict",
    "classify_completeness",
]
