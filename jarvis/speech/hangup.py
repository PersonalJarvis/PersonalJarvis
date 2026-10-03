"""Shared voice hang-up requests and host-owned two-turn confirmation.

Desktop speech, realtime voice and telephony all require an explicit answer
following the host's question. Complete utterances are matched, never closing
words inside a task or quotation. Model sentinels and legacy farewells remain
recognizable for diagnostics and stripping, but cannot authorize termination.
This module uses only the standard library so every voice surface can import it.
"""
from __future__ import annotations

import re
import time
from typing import Final

# Closing-command candidates. Mishearings only trigger a question; the final
# matcher below requires the complete utterance, never a substring of a task.
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

# A closing word inside a task, quotation or negation is never call control.
HANGUP_RE: Final[re.Pattern[str]] = re.compile(
    r"\A\s*(?:(?:please|bitte|okay|ok|jarvis|can you|could you|"  # i18n-allow
    r"kannst du|könntest du|du kannst)[\s,]+)*"  # i18n-allow
    r"(?:" + "|".join(_HANGUP_PATTERNS) + r")"
    r"(?:[\s,]+(?:please|bitte|now|jetzt|jarvis))*[\s.!?]*\Z",  # i18n-allow
    re.IGNORECASE,
)


def matched_hangup_pattern(text: str) -> str | None:
    """Identify the static matching branch for diagnostics without user text."""
    if HANGUP_RE.fullmatch(text) is None:
        return None
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
    r"|das war'?s(?: für heute)?|das war alles(?: für heute)?"  # i18n-allow
    r"|das ist alles|mehr brauche ich nicht"  # i18n-allow
    r"|that'?s it(?: for (?:now|today))?"
    r"|keine weiteren fragen|ich muss (?:los|gehen)|du kannst gehen"  # i18n-allow
    r"|eso es todo(?: por (?:ahora|hoy))?|hemos terminado|ya terminamos"
    r"|no necesito nada más|no tengo más preguntas|me tengo que ir"
    r"|cuelga|adiós|adios|hasta luego"
    r")"
    r"(?:[\s,.!]+(?:thanks|thank you|danke|gracias|jarvis))*[\s.!]*",  # i18n-allow
    re.IGNORECASE,
)


def supports_semantic_hangup(user_text: str | None) -> bool:
    """Recognize a complete conversational closing that warrants a question."""
    text = (user_text or "").replace("’", "'")
    return _SEMANTIC_CLOSING_RE.fullmatch(text) is not None


def user_asked_to_hang_up(user_text: str | None) -> bool:
    """Recognize a user request; this is never sufficient to end a call.

    A model tool call alone never ends a call. Live 2026-10-01: the user
    answered "Ja" to an approval and the model called end_call, dropping the
    call and the approved task. A missed hang-up costs one repeat; a false one
    kills the session, so only an explicit command or a complete closing
    speech act counts.
    """
    text = user_text or ""
    return bool(HANGUP_RE.fullmatch(text)) or supports_semantic_hangup(text)


_CONFIRM_HANGUP_RE = re.compile(
    r"\s*(?:yes|yeah|yep|ja|sí|si)"  # i18n-allow
    r"(?:[\s,]+(?:please|bitte|por favor|hang up|end the call|"  # i18n-allow
    r"auflegen|leg auf|beende das gespräch|cuelga))*[\s.!]*",  # i18n-allow
    re.IGNORECASE,
)


def confirms_hangup(text: str) -> bool:
    """Recognize a complete affirmative answer, never a prefix of a correction."""
    return _CONFIRM_HANGUP_RE.fullmatch(text) is not None


def hangup_confirmation_question(language: str) -> str:
    """Use the caller's already resolved output language."""
    return {
        "de": "Möchtest du wirklich auflegen?",  # i18n-allow
        "es": "¿De verdad quieres colgar?",
    }.get(language.split("-")[0].lower(), "Do you really want to hang up?")


def hangup_cancelled_reply(language: str) -> str:
    return {
        "de": "Okay, ich bleibe dran.",  # i18n-allow
        "es": "Vale, seguimos hablando.",
    }.get(language.split("-")[0].lower(), "Okay, I'll stay on the call.")


class HangupConfirmation:
    """A voice request needs a separate answer to our own recent question.

    The surface arms this only after it has delivered the question. Model
    output, repeated tool calls and same-turn transcript fragments cannot arm
    or confirm it. An unrelated next utterance cancels it, as does expiry.
    """

    def __init__(self) -> None:
        self.pending_turn: object | None = None
        self.expires_at = 0.0

    def reset(self) -> None:
        self.pending_turn = None
        self.expires_at = 0.0

    def arm(self, turn: object) -> None:
        self.pending_turn = turn
        self.expires_at = time.monotonic() + 30.0

    def observe(self, text: str, turn: object) -> str:
        pending = self.pending_turn
        if pending is not None and time.monotonic() >= self.expires_at:
            self.reset()
            pending = None
        requested = user_asked_to_hang_up(text)
        if pending == turn:
            if requested:
                return "waiting"
            self.reset()
            return ""
        if pending is not None:
            self.reset()
            if confirms_hangup(text):
                return "confirmed"
            if re.fullmatch(
                r"\s*(?:no|nope|nein|cancel|abbrechen)[\s.!]*",  # i18n-allow
                text, re.I,
            ):
                return "cancelled"
        return "request" if requested else ""


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
    "HangupConfirmation",
    "hangup_confirmation_question",
    "hangup_cancelled_reply",
    "LEGACY_FAREWELL_PHRASES",
    "contains_end_signal",
    "is_legacy_farewell",
    "matched_hangup_pattern",
    "strip_end_signal",
    "supports_semantic_hangup",
    "user_asked_to_hang_up",
]
