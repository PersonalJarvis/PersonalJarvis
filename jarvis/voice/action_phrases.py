"""Localized phrases for the deterministic computer-use / local-action paths.

These run OFF the LLM — the local-action fast path and the background
computer-use offload — so the brain's language-pin machinery (which only
governs LLM-generated replies) never touches them. Each phrase is spoken or
displayed VERBATIM, so it must be rendered in the turn's language HERE (live
bug 2026-06-15: an all-English computer-use turn ended with the German
"Erledigt." completion readback).

Pure dict lookups — no LLM, no IO (AP-9 / AP-11). Languages: de / en / es;
an unknown language falls back to German (the historical default). The German
column is intentionally the same wording the paths used before, so a German
turn is byte-identical to the old behavior.
"""
from __future__ import annotations

from jarvis.core.turn_language import resolve_turn_language

_DEFAULT = "de"
_SUPPORTED = ("de", "en", "es")

# key -> {lang -> template}. Templates may carry named ``{fields}``.
_PHRASES: dict[str, dict[str, str]] = {
    # Computer-use background offload — outcome readbacks (announcement bus).
    "cu_done": {
        "de": "Erledigt.",  # i18n-allow
        "en": "Done.",
        "es": "Listo.",
    },
    "cu_failed": {
        "de": "Das am Bildschirm hat nicht geklappt.",  # i18n-allow
        "en": "That didn't work on screen.",
        "es": "Eso no funcionó en la pantalla.",
    },
    "cu_failed_reason": {
        "de": "Das am Bildschirm hat nicht geklappt: {error}",  # i18n-allow
        "en": "That didn't work on screen: {error}",
        "es": "Eso no funcionó en la pantalla: {error}",
    },
    "cu_crashed": {
        "de": "Beim Erledigen am Bildschirm ist leider etwas schiefgegangen.",  # i18n-allow
        "en": "Something went wrong while doing it on screen.",
        "es": "Algo salió mal al hacerlo en la pantalla.",
    },
    "cu_timeout": {
        "de": "Das am Bildschirm hat zu lange gedauert "  # i18n-allow
              "(ueber {secs} Sekunden) und wurde abgebrochen.",  # i18n-allow
        "en": "That took too long on screen (over {secs} seconds) and was cancelled.",
        "es": "Eso tardó demasiado en la pantalla (más de {secs} segundos) y se canceló.",
    },
    # Computer-use dispatch — the immediate optimistic ACK.
    "cu_dispatch_ack": {
        "de": "Mach ich — ich erledige das direkt am Bildschirm "  # i18n-allow
              "und sage Bescheid, sobald es fertig ist.",  # i18n-allow
        "en": "On it — I'll handle that on screen and let you know when it's done.",
        "es": "Voy — lo hago directamente en la pantalla y te aviso cuando termine.",
    },
    # Cost / budget guards on the computer-use branch.
    "cost_cooldown": {
        "de": "Cost-Cooldown aktiv — Tagesbudget erschoepft. "  # i18n-allow
              "Neue Anfragen werden erst nach dem Cooldown-Ende bearbeitet.",  # i18n-allow
        "en": "Cost cooldown active — the daily budget is used up. "
              "New requests resume once the cooldown ends.",
        "es": "Enfriamiento de costes activo — el presupuesto diario se agotó. "
              "Las nuevas solicitudes se reanudan al terminar el enfriamiento.",
    },
    "task_budget": {
        "de": "Task-Budget fuer diese Konversation ueberschritten.",  # i18n-allow
        "en": "The task budget for this conversation is exceeded.",
        "es": "Se superó el presupuesto de tareas de esta conversación.",
    },
    "daily_budget": {
        "de": "Tagesbudget ueberschritten.",  # i18n-allow
        "en": "The daily budget is exceeded.",
        "es": "Se superó el presupuesto diario.",
    },
    # Direct local-action tool failure fallback.
    "tool_failed": {
        "de": "{tool} fehlgeschlagen.",  # i18n-allow
        "en": "{tool} failed.",
        "es": "{tool} falló.",
    },
}


def resolve_phrase_language(reply_language: str | None, user_text: str) -> str:
    """Resolve de/en/es for a deterministic action phrase.

    An explicit ``brain.reply_language`` pin (de/en/es) wins; otherwise the
    turn's language is detected from ``user_text``; ambiguous text keeps the
    historical German default. Mirrors ``BrainManager._direct_ack_language`` so
    every non-LLM spoken path resolves language the same way.
    """
    if reply_language in _SUPPORTED:
        return str(reply_language)
    return resolve_turn_language("unknown", user_text, default=_DEFAULT)


def action_phrase(key: str, lang: str, **fmt: object) -> str:
    """Render the localized phrase ``key`` in ``lang`` (de/en/es).

    Unknown languages fall back to German. ``fmt`` fills any named template
    fields (e.g. ``error``, ``secs``, ``tool``).
    """
    variants = _PHRASES[key]
    template = variants.get(lang) or variants[_DEFAULT]
    return template.format(**fmt) if fmt else template


__all__ = ["action_phrase", "resolve_phrase_language"]
