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

from jarvis.core.turn_language import DEFAULT_LOCALE, localized, normalize_language_tag

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
        "ja": (
            "\u672c\u5f53\u306b\u30e1\u30fc\u30eb\u3092\u9001\u4fe1\u3057\u307e\u3059\u304b\uff1f"
            "\u300c\u306f\u3044\u300d\u304b\u300c\u3044\u3044\u3048\u300d\u3067\u7b54\u3048\u3066"
            "\u304f\u3060\u3055\u3044\u3002"
        ),
    },
    "gmail_rest": {
        "de": "Soll ich die E-Mail wirklich senden? Sag ja oder nein.",
        "en": "Do you really want me to send the email? Say yes or no.",
        "es": "¿Quieres que envíe el correo de verdad? Di sí o no.",
        "ja": (
            "\u672c\u5f53\u306b\u30e1\u30fc\u30eb\u3092\u9001\u4fe1\u3057\u307e\u3059\u304b\uff1f"
            "\u300c\u306f\u3044\u300d\u304b\u300c\u3044\u3044\u3048\u300d\u3067\u7b54\u3048\u3066"
            "\u304f\u3060\u3055\u3044\u3002"
        ),
    },
    "call-contact": {
        "de": "Soll ich den Anruf wirklich starten? Sag ja oder nein.",
        "en": "Do you really want me to place the call? Say yes or no.",
        "es": "¿Quieres que haga la llamada de verdad? Di sí o no.",
        "ja": (
            "\u672c\u5f53\u306b\u96fb\u8a71\u3092\u304b\u3051\u307e\u3059\u304b\uff1f\u300c\u306f"
            "\u3044\u300d\u304b\u300c\u3044\u3044\u3048\u300d\u3067\u7b54\u3048\u3066\u304f\u3060"
            "\u3055\u3044\u3002"
        ),
    },
}

_GENERIC_QUESTION: dict[str, str] = {
    "de": "Soll ich das wirklich ausführen? Sag ja oder nein.",
    "en": "Do you really want me to do that? Say yes or no.",
    "es": "¿Quieres que lo haga de verdad? Di sí o no.",
    "ja": (
        "\u672c\u5f53\u306b\u5b9f\u884c\u3057\u307e\u3059\u304b\uff1f\u300c\u306f\u3044\u300d\u304b"
        "\u300c\u3044\u3044\u3048\u300d\u3067\u7b54\u3048\u3066\u304f\u3060\u3055\u3044\u3002"
    ),
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
        "ja": (
            "\u6ce8\u610f\u3057\u3066\u304f\u3060\u3055\u3044\u3002\u3053\u306e\u30b3\u30de\u30f3"
            "\u30c9\u306f\u4f55\u304b\u3092\u524a\u9664\u3057\u307e\u3059\uff08{commands}\uff09"
            "\u3002\u672c\u5f53\u306b\u5b9f\u884c\u3057\u307e\u3059\u304b\uff1f\u300c\u306f\u3044"
            "\u300d\u304b\u300c\u3044\u3044\u3048\u300d\u3067\u7b54\u3048\u3066\u304f\u3060\u3055"
            "\u3044\u3002"
        ),
    },
    "modify": {
        "de": ("Dieser Befehl würde etwas auf dem Computer verändern "
               "({commands}). Soll ich ihn ausführen? Sag ja oder nein."),
        "en": ("This command would change something on the computer "
               "({commands}). Do you want me to run it? Say yes or no."),
        "es": ("Este comando cambiaría algo en el equipo ({commands}). "
               "¿Quieres que lo ejecute? Di sí o no."),
        "ja": (
            "\u3053\u306e\u30b3\u30de\u30f3\u30c9\u306fPC\u306e\u4e2d\u8eab\u3092\u5909\u66f4\u3057"
            "\u307e\u3059\uff08{commands}\uff09\u3002\u5b9f\u884c\u3057\u307e\u3059\u304b\uff1f"
            "\u300c\u306f\u3044\u300d\u304b\u300c\u3044\u3044\u3048\u300d\u3067\u7b54\u3048\u3066"
            "\u304f\u3060\u3055\u3044\u3002"
        ),
    },
    "read": {
        "de": ("Dieser Befehl liest nur Daten ({commands}). "
               "Soll ich ihn ausführen? Sag ja oder nein."),
        "en": ("This command only reads data ({commands}). "
               "Do you want me to run it? Say yes or no."),
        "es": ("Este comando solo lee datos ({commands}). "
               "¿Quieres que lo ejecute? Di sí o no."),
        "ja": (
            "\u3053\u306e\u30b3\u30de\u30f3\u30c9\u306f\u30c7\u30fc\u30bf\u3092\u8aad\u3080\u3060"
            "\u3051\u3067\u3059\uff08{commands}\uff09\u3002\u5b9f\u884c\u3057\u307e\u3059\u304b"
            "\uff1f\u300c\u306f\u3044\u300d\u304b\u300c\u3044\u3044\u3048\u300d\u3067\u7b54\u3048"
            "\u3066\u304f\u3060\u3055\u3044\u3002"
        ),
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
        template = localized(_IMPACT_QUESTIONS[impact_level], lang)
        commands = " ".join((impact_commands or "").split())
        commands = commands[:_IMPACT_COMMANDS_MAX_CHARS].strip()
        if commands:
            return template.format(commands=commands)
        return template.replace(" ({commands})", "")
    table = _TOOL_QUESTIONS.get(tool_name)
    if table is not None and lang in table:
        return table[lang]
    return localized(_GENERIC_QUESTION, lang)


# ----------------------------------------------------------------------
# Outcome phrasing (spoken on turn N+1 after the user answers).
# ----------------------------------------------------------------------

_OUTCOME: dict[str, dict[str, str]] = {
    "done": {
        "de": "Erledigt.",
        "en": "Done.",
        "es": "Listo.",
        "ja": "\u5b8c\u4e86\u3057\u307e\u3057\u305f\u3002",
    },
    "vetoed": {
        "de": "Okay, lass ich.",
        "en": "Okay, leaving it.",
        "es": "Vale, lo dejo.",
        "ja": (
            "\u308f\u304b\u308a\u307e\u3057\u305f\u3001\u3084\u3081\u3066\u304a\u304d\u307e\u3059"
            "\u3002"
        ),
    },
    "timeout": {
        "de": "Hab keine Antwort gehört, ich lass es.",
        "en": "No answer heard, leaving it.",
        "es": "No te he oído, lo dejo.",
        "ja": (
            "\u8fd4\u4e8b\u304c\u805e\u3053\u3048\u306a\u304b\u3063\u305f\u306e\u3067\u3001\u3084"
            "\u3081\u3066\u304a\u304d\u307e\u3059\u3002"
        ),
    },
    "failed": {
        "de": "Das hat nicht geklappt.",
        "en": "That didn't work.",
        "es": "Eso no funcionó.",
        "ja": "\u3046\u307e\u304f\u3044\u304d\u307e\u305b\u3093\u3067\u3057\u305f\u3002",
    },
    "unclear": {
        "de": "Sag bitte einfach ja oder nein.",
        "en": "Please just say yes or no.",
        "es": "Di simplemente sí o no, por favor.",
        "ja": (
            "\u300c\u306f\u3044\u300d\u304b\u300c\u3044\u3044\u3048\u300d\u3067\u7b54\u3048\u3066"
            "\u304f\u3060\u3055\u3044\u3002"
        ),
    },
    # Nobody could be asked at all — an unattended run (a scheduled workflow,
    # a cron job, a one-shot CLI call) reached a consequential tool. Says why
    # nothing happened without claiming a refusal that never took place
    # (audit GT-12). Read from a run log far more often than spoken.
    "unavailable": {
        "de": "Dafür brauche ich deine Freigabe, und hier kann sie niemand geben. "
              "Ich habe es nicht gemacht.",
        "en": "That needs your approval, and nobody here can give it. "
              "So I did not do it.",
        "es": "Eso necesita tu aprobación y aquí nadie puede darla. "
              "Así que no lo hice.",
        "ja": (
            "\u3053\u308c\u306f\u3042\u306a\u305f\u306e\u627f\u8a8d\u304c\u5fc5\u8981\u3067\u3059"
            "\u304c\u3001\u627f\u8a8d\u3067\u304d\u308b\u4eba\u304c\u3044\u307e\u305b\u3093\u3002"
            "\u306a\u306e\u3067\u5b9f\u884c\u3057\u3066\u3044\u307e\u305b\u3093\u3002"
        ),
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


def format_approval_unavailable(
    tool_name: str, *, language: str = DEFAULT_LOCALE
) -> str:
    """Render why a consequential action did not run when NOBODY could approve.

    Used by ``ToolExecutor`` on an unattended surface (a scheduled workflow, a
    cron run, a one-shot CLI call), where the two-turn confirmation above has
    no second turn to happen in. Deliberately a sibling of
    ``format_confirm_outcome`` over the same table: "the user said no",
    "nobody answered in time", and "there was nobody to ask" are three
    different facts, and the wording keeps them apart.

    ``tool_name`` is accepted for future tool-specific wording and is NOT
    interpolated today — a raw internal identifier ("gmail_rest") explains
    nothing to a person and has no place in a spoken line.
    """
    return format_confirm_outcome("unavailable", tool_name, language=language)
