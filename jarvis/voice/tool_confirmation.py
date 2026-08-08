"""Generic, channel-agnostic confirmation phrasing for an ``ask``-tier tool run
through the two-turn confirmation flow.

Why this exists (forensic 2026-06-18, session 2995997b): an ``ask``-tier tool
(gmail send) invoked on the voice path blocks in ``ApprovalWorkflow.wait()`` for a
UI approval the voice user never gives. The 20 s no-first-frame ceiling then
beheads the working turn and Jarvis speaks the brain-timeout fallback. There is
no voice/chat path to APPROVE a consequential action today (``jarvis/speech``
never publishes ``ActionApproved``).

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


# Impact-aware questions for shell commands (explain layer, 2026-08-08): a
# non-technical user hears WHAT the command would do, not just "run that?".
# ``{commands}`` is DATA (the classified command words, e.g. "rm"), not
# re-localized phrasing — same doctrine as the failed-outcome detail below.
_IMPACT_QUESTIONS: dict[str, dict[str, str]] = {
    "destructive": {
        "de": ("Achtung, dieser Befehl würde etwas löschen ({commands}). "
               "Soll ich ihn wirklich ausführen? Sag ja oder nein."),
        "en": ("Careful, this command would delete something ({commands}). "
               "Do you really want me to run it? Say yes or no."),
        "es": ("Cuidado, este comando borraría algo ({commands}). "
               "¿Quieres que lo ejecute de verdad? Di sí o no."),
    },
    "modify": {
        "de": ("Dieser Befehl würde etwas auf dem Computer verändern "
               "({commands}). Soll ich ihn ausführen? Sag ja oder nein."),
        "en": ("This command would change something on the computer "
               "({commands}). Do you want me to run it? Say yes or no."),
        "es": ("Este comando cambiaría algo en el equipo ({commands}). "
               "¿Quieres que lo ejecute? Di sí o no."),
    },
    "read": {
        "de": ("Dieser Befehl liest nur Daten ({commands}). "
               "Soll ich ihn ausführen? Sag ja oder nein."),
        "en": ("This command only reads data ({commands}). "
               "Do you want me to run it? Say yes or no."),
        "es": ("Este comando solo lee datos ({commands}). "
               "¿Quieres que lo ejecute? Di sí o no."),
    },
}

_IMPACT_COMMANDS_MAX_CHARS = 60


def format_tool_confirmation(
    tool_name: str,
    *,
    language: str = "de",
    impact_level: str | None = None,
    impact_commands: str | None = None,
) -> str:
    """Render the spoken/written confirmation question for ``tool_name``.

    When the deferring tool supplied an impact classification (see
    ``describe_args`` in ``jarvis/plugins/tool/run_shell.py``), the question
    states in plain language what the command would do. An unknown
    ``impact_level`` is ignored — phrasing must never fail. Falls back to a
    tool-specific, then a generic question, and to ``DEFAULT_LOCALE`` for an
    unrecognised language tag. Never returns "".
    """
    lang = _phrase_lang(language)
    if impact_level in _IMPACT_QUESTIONS:
        template = _IMPACT_QUESTIONS[impact_level][lang]
        commands = " ".join((impact_commands or "").split())
        commands = commands[:_IMPACT_COMMANDS_MAX_CHARS].strip()
        if commands:
            return template.format(commands=commands)
        return template.replace(" ({commands})", "")
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


_FAILED_DETAIL_MAX_CHARS = 160


def format_confirm_outcome(
    kind: str, tool_name: str, *, language: str = "de", detail: str | None = None
) -> str:
    """Render the outcome phrase after the user answered a confirmation.

    ``kind`` ∈ {"done", "vetoed", "timeout", "failed", "unclear"}. ``tool_name``
    is accepted for future tool-specific wording; the current phrasing is generic
    but always non-empty (AD-OE6) and covers de/en/es.

    ``detail`` is appended only on ``kind="failed"``: a confirmed action that
    fails with a bare "that didn't work" gives the user nothing to correct
    with (forensic 2026-07-13 18:33 — the actionable "no MCP server named
    'github'" reason was swallowed). The detail is DATA (like a filename),
    not re-localized phrasing; it is whitespace-collapsed and bounded.
    """
    lang = _phrase_lang(language)
    table = _OUTCOME.get(kind)
    if table is None:  # unknown kind — honest, never empty
        table = _OUTCOME["failed"]
    phrase = table.get(lang, table[DEFAULT_LOCALE])
    if kind == "failed" and detail:
        cleaned = " ".join(str(detail).split())[:_FAILED_DETAIL_MAX_CHARS].strip()
        if cleaned:
            phrase = f"{phrase} {cleaned}"
    return phrase
