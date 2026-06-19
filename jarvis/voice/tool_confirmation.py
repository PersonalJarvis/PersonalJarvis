"""Generic, channel-agnostic confirmation phrasing for an ``ask``-tier tool run
through the two-turn confirmation flow.

Why this exists (forensic 2026-06-18, session 2995997b): an ``ask``-tier tool
(gmail send) invoked on the voice path blocks in ``ApprovalWorkflow.wait()`` for a
UI approval the voice user never gives. The 20 s no-first-frame ceiling then
beheads the working turn and Jarvis speaks the misleading "Das hat gerade zu lange
gedauert. Sag es bitte noch einmal." There is no voice/chat path to APPROVE a
consequential action today (``jarvis/speech`` never publishes ``ActionApproved``).

Instead of hanging, the brain now SPEAKS a short confirmation question on turn N
and the user's next "ja"/"nein" (classified by ``echo_confirmation.classify_
response``) resolves it on turn N+1. This module owns only the deterministic
PHRASING — no LLM call (AP-11), no I/O. The yes/no classifier is shared with the
self-mod flow (``jarvis.voice.echo_confirmation``).

Runtime Output Language doctrine (CLAUDE.md): every spoken phrase table carries
de / en / es; an unrecognised tag resolves through ``DEFAULT_LOCALE`` — never an
empty string (AD-OE6 zero-silent-drop), never a per-layer hardcoded constant.
"""
from __future__ import annotations

from jarvis.core.turn_language import DEFAULT_LOCALE, normalize_language_tag

_PHRASE_LANGS: frozenset[str] = frozenset({"de", "en", "es"})


def _phrase_lang(language: str | None) -> str:
    """Normalize a language tag to a phrase key ("de"/"en"/"es"), else default."""
    code = normalize_language_tag(language)
    return code if code in _PHRASE_LANGS else DEFAULT_LOCALE


# ----------------------------------------------------------------------
# Confirmation questions (End-Focus: the action sits late so an STT misshear
# is obvious to the user before they say "ja").
# ----------------------------------------------------------------------

# Tool-specific questions keyed by tool name → {lang: question}. A tool that is
# not mapped here falls back to the generic question below.
_TOOL_QUESTIONS: dict[str, dict[str, str]] = {
    "gmail": {
        "de": "Soll ich die E-Mail wirklich senden? Sag ja oder nein.",
        "en": "Do you really want me to send the email? Say yes or no.",
        "es": "¿Quieres que envíe el correo de verdad? Di sí o no.",
    },
    "gmail_rest": {
        "de": "Soll ich die E-Mail wirklich senden? Sag ja oder nein.",
        "en": "Do you really want me to send the email? Say yes or no.",
        "es": "¿Quieres que envíe el correo de verdad? Di sí o no.",
    },
    "call-contact": {
        "de": "Soll ich den Anruf wirklich starten? Sag ja oder nein.",
        "en": "Do you really want me to place the call? Say yes or no.",
        "es": "¿Quieres que haga la llamada de verdad? Di sí o no.",
    },
}

_GENERIC_QUESTION: dict[str, str] = {
    "de": "Soll ich das wirklich ausführen? Sag ja oder nein.",
    "en": "Do you really want me to do that? Say yes or no.",
    "es": "¿Quieres que lo haga de verdad? Di sí o no.",
}


def format_tool_confirmation(tool_name: str, *, language: str = "de") -> str:
    """Render the spoken/written confirmation question for ``tool_name``.

    Falls back to a generic question for any tool without a specific entry, and
    to ``DEFAULT_LOCALE`` for an unrecognised language tag. Never returns "".
    """
    lang = _phrase_lang(language)
    table = _TOOL_QUESTIONS.get(tool_name)
    if table is not None and lang in table:
        return table[lang]
    return _GENERIC_QUESTION[lang]


# ----------------------------------------------------------------------
# Outcome phrasing (spoken on turn N+1 after the user answers).
# ----------------------------------------------------------------------

_OUTCOME: dict[str, dict[str, str]] = {
    "done": {
        "de": "Erledigt.",
        "en": "Done.",
        "es": "Listo.",
    },
    "vetoed": {
        "de": "Okay, lass ich.",
        "en": "Okay, leaving it.",
        "es": "Vale, lo dejo.",
    },
    "timeout": {
        "de": "Hab keine Antwort gehört, ich lass es.",
        "en": "No answer heard, leaving it.",
        "es": "No te he oído, lo dejo.",
    },
    "failed": {
        "de": "Das hat nicht geklappt.",
        "en": "That didn't work.",
        "es": "Eso no funcionó.",
    },
    "unclear": {
        "de": "Sag bitte einfach ja oder nein.",
        "en": "Please just say yes or no.",
        "es": "Di simplemente sí o no, por favor.",
    },
}


def format_confirm_outcome(
    kind: str, tool_name: str, *, language: str = "de"
) -> str:
    """Render the outcome phrase after the user answered a confirmation.

    ``kind`` ∈ {"done", "vetoed", "timeout", "failed", "unclear"}. ``tool_name``
    is accepted for future tool-specific wording; the current phrasing is generic
    but always non-empty (AD-OE6) and covers de/en/es.
    """
    lang = _phrase_lang(language)
    table = _OUTCOME.get(kind)
    if table is None:  # unknown kind — honest, never empty
        table = _OUTCOME["failed"]
    return table.get(lang, table[DEFAULT_LOCALE])
