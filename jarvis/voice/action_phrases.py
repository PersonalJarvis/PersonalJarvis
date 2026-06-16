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

import re

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
    # Computer-use failure readbacks keyed off the harness EXIT CODE. These are
    # plain-language sentences for the case where the underlying layer gives us
    # only an opaque "exit N" token (no human reason). They exist so the user
    # NEVER hears a raw exit code — live bug: the spoken readback was "That
    # didn't work on screen: exit 5" and the user asked "what is the exit file?".
    # Exit-code semantics are documented at the top of
    # jarvis/harness/screenshot_only_loop.py (5=`fail`, 1=observe, 2=parse,
    # 4=step budget, 8=tool failure, 124=timeout, 130=cancel).
    "cu_exit_gave_up": {  # exit 5 — the model's `fail` action
        "de": "Ich habe es am Bildschirm versucht, aber nicht hinbekommen.",  # i18n-allow
        "en": "I tried it on screen but couldn't get it done.",
        "es": "Lo intenté en la pantalla, pero no pude completarlo.",
    },
    "cu_exit_no_view": {  # exit 1 — observe failure (no usable screenshot)
        "de": "Ich konnte den Bildschirm nicht richtig sehen und habe abgebrochen.",  # i18n-allow
        "en": "I couldn't see the screen properly, so I stopped.",
        "es": "No pude ver bien la pantalla, así que lo detuve.",
    },
    "cu_exit_confused": {  # exit 2 — parse error
        "de": "Ich bin am Bildschirm durcheinandergekommen und habe abgebrochen.",  # i18n-allow
        "en": "I got confused on screen and had to stop.",
        "es": "Me confundí en la pantalla y tuve que detenerme.",
    },
    "cu_exit_too_many_steps": {  # exit 4 — step budget exhausted
        "de": "Es hat am Bildschirm zu viele Schritte gebraucht, ich habe "  # i18n-allow
              "aufgehoert.",  # i18n-allow
        "en": "It took too many steps on screen, so I stopped.",
        "es": "Hicieron falta demasiados pasos en la pantalla, así que lo detuve.",
    },
    "cu_exit_action_failed": {  # exit 8 — tool/action failure
        "de": "Eine Aktion am Bildschirm ist fehlgeschlagen, ich habe abgebrochen.",  # i18n-allow
        "en": "An action on screen failed, so I stopped.",
        "es": "Una acción en la pantalla falló, así que lo detuve.",
    },
    "cu_exit_cancelled": {  # exit 130 — cancel token (e.g. voice hangup)
        "de": "Die Aktion am Bildschirm wurde abgebrochen.",  # i18n-allow
        "en": "The action on screen was cancelled.",
        "es": "La acción en la pantalla se canceló.",
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


# A bare "exit N" token, optionally wrapped in brackets/whitespace, with nothing
# else of substance around it — the opaque string ``dispatch_to_harness`` emits
# (``f"exit {exit_code}"``). This must never be spoken to the user.
_BARE_EXIT_RE = re.compile(r"^\s*\(?\s*exit\s*\d+\s*\)?\s*$", re.IGNORECASE)
#: Static map: harness exit code -> generic plain-language phrase key. Keep in
#: sync with the exit-code legend in jarvis/harness/screenshot_only_loop.py.
_EXIT_CODE_PHRASE: dict[int, str] = {
    1: "cu_exit_no_view",
    2: "cu_exit_confused",
    4: "cu_exit_too_many_steps",
    5: "cu_exit_gave_up",
    8: "cu_exit_action_failed",
    130: "cu_exit_cancelled",
}
#: Strip the loop's "[cu] <verb> at <tag>: " prefix so the human reason the
#: model gave for ``fail`` surfaces clean (the loop writes
#: ``"[cu] fail at step-N: <reason>"`` to stderr).
_CU_REASON_PREFIX_RE = re.compile(
    r"^\s*\[cu\][^:]*:\s*", re.IGNORECASE,
)


def _looks_human(text: str) -> bool:
    """True if ``text`` is a real reason sentence, not an opaque exit token.

    Defensive against the upstream layer: when a parallel change makes the
    harness forward the model's ``fail`` reason, we forward it; only a bare
    ``exit N`` / purely-numeric / empty error gets replaced by a generic phrase.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _BARE_EXIT_RE.match(stripped):
        return False
    # A token that is only digits / punctuation carries no human meaning.
    if not re.search(r"[A-Za-zÀ-ÿ]", stripped):
        return False
    return True


def cu_failure_readback(
    lang: str,
    *,
    error: str | None,
    exit_code: int | None = None,
    detail: str | None = None,
) -> str:
    """Compose the spoken/chat readback for a Computer-Use FAILURE.

    The user must NEVER hear a raw exit code (live bug: "That didn't work on
    screen: exit 5"). This is a pure STATIC lookup — no LLM call (AP-11).

    Resolution order, most-specific first:

    1. ``detail`` (e.g. the harness ``stderr`` carrying the model's ``fail``
       reason) — if it contains a human sentence, FORWARD it (it gets scrubbed
       downstream). The loop's ``"[cu] fail at <tag>: "`` prefix is stripped.
    2. ``error`` — if it is already a human sentence (not a bare ``exit N`` /
       numeric token), FORWARD it.
    3. Otherwise substitute the generic, localized phrase keyed off
       ``exit_code`` (``cu_exit_*``), falling back to the plain
       "didn't work on screen" sentence when the code is unknown.
    """
    # 1) The harness detail string may carry the model's verified reason.
    if detail:
        candidate = _CU_REASON_PREFIX_RE.sub("", detail).strip()
        if _looks_human(candidate):
            return action_phrase("cu_failed_reason", lang, error=candidate)

    # 2) A human reason already on the error field is forwarded verbatim.
    if _looks_human(error or ""):
        return action_phrase("cu_failed_reason", lang, error=str(error).strip())

    # 3) Opaque / empty error -> map the exit code to a human phrase.
    phrase_key = _EXIT_CODE_PHRASE.get(int(exit_code)) if exit_code is not None else None
    if phrase_key is None:
        # Unknown / missing exit code: the neutral "didn't work on screen".
        return action_phrase("cu_failed", lang)
    return action_phrase(phrase_key, lang)


__all__ = ["action_phrase", "cu_failure_readback", "resolve_phrase_language"]
