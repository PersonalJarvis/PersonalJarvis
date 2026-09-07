"""Shared hang-up intent detection — single source of truth for both voice surfaces.

Two surfaces end a voice session: the desktop microphone pipeline
(``jarvis/speech/pipeline.py``) and Twilio telephony
(``jarvis/telephony/session.py``). They used to carry two separate, drifting
copies of the hang-up regex, and the microphone path additionally matched a
fragile *exact* farewell string emitted by the brain.

This module unifies both:

1. ``HANGUP_RE`` — explicit, unambiguous closing **commands** in German and
   English, matched against the transcript BEFORE the brain is called. Fast and
   deterministic. Deliberately narrow: ambiguous-polite phrases (a bare "thank
   you", "das war's") are NOT here — they are delegated to the brain, which has
   conversational context and a conservative "stay on when unsure" mandate.

2. ``END_CALL_SIGNAL`` — a control sentinel the brain appends to its reply when
   it judges the user wants to end (see ``JARVIS_PERSONA.md``). The pipeline
   detects it on the RAW brain response and strips it before TTS, so the brain
   may phrase the farewell naturally instead of emitting a magic string.

3. ``is_legacy_farewell`` — backward compatibility for the old exact phrases
   ("auf wiedersehen, ruben" / "goodbye, ruben"), so a brain instance still
   running the previous persona contract continues to hang up during rollout.

Standard-library only (``re``). It must stay free of ``sounddevice`` and any
heavy import so the telephony path can import it (``jarvis/speech/__init__.py``
is intentionally empty, so importing ``jarvis.speech.hangup`` pulls in nothing
else).
"""
from __future__ import annotations

import re
from typing import Final

# --- Explicit closing commands (pre-brain, instant) -----------------------
# Bilingual. Whisper mis-transcribes "auflegen" in many ways, so the German
# "auflegen" morphology is matched generously — it is the single most-used
# command and a false negative there is the worst failure. Ambiguous-polite
# phrases ("vielen dank", "danke jarvis", "das war's") are intentionally
# absent: they are handled by the brain under the stay-on-when-unsure mandate.
_HANGUP_PATTERNS: Final[tuple[str, ...]] = (
    # German — auflegen morphology + Whisper split/mis-hearing variants
    r"\bauflegen\b",
    r"\bauf\s*legen\b",
    r"\baufleg\w*\b",
    r"\bauf\s+leg\w*\b",   # "auf leg", "auf lege" — Whisper splits "auflegen"
    r"\bauf\s+legt\b",
    # This lossy STT alias is safe only as the complete utterance. Keeping it
    # unanchored caused a live language-switch request containing "auf jetzt"
    # to terminate before the brain ran (2026-07-12). Optional discourse
    # fillers preserve the original one-word-command recovery without matching
    # the same words inside ordinary speech.
    r"^\s*(?:(?:okay|ok|bitte)[\s,]+)*auf\s+jetzt[\s.!?]*$",  # i18n-allow: STT input
    r"\bleg(e|t|en)?\s+auf\b",
    r"\blegs?\s+auf\b",
    r"\blegen sie auf\b",
    r"\baufgelegt\b",
    r"\bdrauf\s*leg\w*\b",
    r"\bableg\w*\b",
    # Whisper mis-transcribes the one-word command "auflegen" as "auffliegen"
    # (homophone with an inserted "i") or "aufflegen" (doubled "f") on a
    # low-confidence utterance (live 2026-06-09: confidence 0.68 / 0.57). Both
    # slipped past the patterns above ("auffliegen" has no "leg"; "aufflegen"
    # has "auffl", not "aufl"), so the hang-up never fired and the user had to
    # repeat the command three times. These two cover that mis-hearing family.
    r"\bauff?lieg\w*\b",
    r"\bauffleg\w*\b",
    # German — other explicit closings
    r"\btschüss\b",  # i18n-allow
    r"\btschuess\b",
    r"\bbeenden\b",
    r"\bgespräch beenden\b",  # i18n-allow
    r"\bauf wiederhören\b",  # i18n-allow
    r"\bauf wiederhoeren\b",
    r"\bauf wiedersehen\b",
    r"\bbis später\b",  # i18n-allow
    r"\bgute nacht\b",
    r"\bjarvis aus\b",
    r"\bjarvis ende\b",
    r"\bende jarvis\b",
    r"\bschluss jetzt\b",
    r"\bfertig jarvis\b",
    r"\bjarvis fertig\b",  # i18n-allow
    r"\bstopp jarvis\b",
    r"\bjarvis stopp\b",
    # English — explicit closings
    r"\bhang ?up\b",
    r"\bhang up the phone\b",
    r"\bend the call\b",
    r"\bgood ?bye\b",
    r"\bgood ?night\b",
    r"\bbye bye\b",
    r"\bbye jarvis\b",
    r"\bjarvis off\b",
    r"\boff jarvis\b",
    r"\bjavis off\b",
    r"\bshut up jarvis\b",
    r"\bstop jarvis\b",
    r"\bjarvis stop\b",
    r"\bexit\b",
    r"\bquit\b",
    r"\bciao\b",
    # REMOVED 2026-07-07: the "English mis-hearings of auflegen" aliases
    # ("let's get up", "let us get up", "just get up"). Live incident: right
    # after a vosk wake, Groq garbled the 448 ms wake-phrase tail into
    # "Let's get up!" (English, conf 0.69) and the alias instantly hung up
    # the freshly opened session — the wake word appeared "completely
    # broken". Ordinary English phrases are far too easy to hallucinate; a
    # genuinely misheard "auflegen" is still covered by the German mishear
    # family above and by the brain's END_CALL_SIGNAL path (stay-on-when-
    # unsure mandate: a missed hang-up costs one repeat, a false hang-up
    # kills the session).
)

HANGUP_RE: Final[re.Pattern[str]] = re.compile("|".join(_HANGUP_PATTERNS), re.IGNORECASE)


def matched_hangup_pattern(text: str) -> str | None:
    """Identify the static matching branch for diagnostics without user text."""
    return next(
        (pattern for pattern in _HANGUP_PATTERNS if re.search(pattern, text, re.IGNORECASE)),
        None,
    )

# --- Brain control sentinel (post-brain, semantic) ------------------------
END_CALL_SIGNAL: Final[str] = "[[END_CALL]]"


def contains_end_signal(text: str | None) -> bool:
    """True if the brain response carries the hang-up sentinel."""
    return bool(text) and END_CALL_SIGNAL in text


# Semantic closure needs evidence in the USER's turn as well as the model's
# sentinel. Match the complete speech act: an informational request that quotes
# a farewell or describes a finished task must never become session control.
# Gratitude alone is not closing intent, and neither is the assistant saying
# that it has completed the requested work.
_SEMANTIC_CLOSING_RE = re.compile(
    r"\s*(?:(?:okay|ok|thanks|thank you|danke|gracias)[\s,.!]+)*"  # i18n-allow
    r"(?:(?:i think|i guess|ich glaube|ich denke|creo que)[\s,]+)?"  # i18n-allow
    r"(?:"
    r"(?:we are|we're|i am|i'm) (?:done|finished)(?: (?:here|for now|for today))?"
    r"|(?:that is|that's|that was) all(?: (?:for now|for today|i needed))?"
    r"|that will be all|nothing else(?: for now)?|no more questions"
    r"|i (?:have|need) to go|let's stop here|you can go now"
    r"|wir sind (?:durch|fertig)(?: für heute)?"  # i18n-allow
    r"|ich bin (?:durch|fertig)(?: für heute)?"  # i18n-allow
    r"|das war'?s(?: für heute)?|das ist alles|mehr brauche ich nicht"  # i18n-allow
    r"|keine weiteren fragen|ich muss (?:los|gehen)|du kannst gehen"  # i18n-allow
    r"|eso es todo(?: por (?:ahora|hoy))?|hemos terminado|ya terminamos"
    r"|no necesito nada más|no tengo más preguntas|me tengo que ir"
    r")"
    r"(?:[\s,.!]+(?:thanks|thank you|danke|gracias|jarvis))*[\s.!]*",  # i18n-allow
    re.IGNORECASE,
)


def supports_semantic_hangup(user_text: str | None) -> bool:
    """Whether the user independently expressed conversation-closing intent.

    This never hangs up by itself. A model sentinel or legacy farewell must
    agree with it; ambiguous speech stays open and explicit commands continue
    to use the immediate pre-brain matcher.
    """
    text = (user_text or "").replace("’", "'")
    return _SEMANTIC_CLOSING_RE.fullmatch(text) is not None


def strip_end_signal(text: str | None) -> str:
    """Remove the sentinel and trim surrounding whitespace.

    Safe on partial chunks and on the full response. ``scrub_for_voice`` also
    strips the sentinel for the production TTS path; this helper is the direct,
    dependency-free equivalent for call sites that do not scrub.
    """
    if not text:
        return text or ""
    return text.replace(END_CALL_SIGNAL, "").strip()


# --- Legacy exact-farewell fallback (backward compatibility) --------------
LEGACY_FAREWELL_PHRASES: Final[frozenset[str]] = frozenset(
    {
        "goodbye, ruben",
        "goodbye ruben",
        "auf wiedersehen, ruben",
        "auf wiedersehen ruben",
        "goodbye, sir",
        "goodbye sir",
    }
)


def is_legacy_farewell(normalized: str | None) -> bool:
    """True if ``normalized`` equals an old exact farewell phrase.

    ``normalized`` is expected pre-lowered and stripped of trailing ``!``/``.``
    by the caller (``text.strip().rstrip("!.").strip().lower()``).
    """
    return bool(normalized) and normalized in LEGACY_FAREWELL_PHRASES


__all__ = [
    "END_CALL_SIGNAL",
    "HANGUP_RE",
    "LEGACY_FAREWELL_PHRASES",
    "contains_end_signal",
    "is_legacy_farewell",
    "matched_hangup_pattern",
    "strip_end_signal",
    "supports_semantic_hangup",
]
