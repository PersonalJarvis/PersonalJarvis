"""Deterministic, honest readback for an immediate-apply config change (Wave 1.4).

In the voice "apply everything now" path (``auto_apply="all"``) there is no
pre-confirmation, so the line spoken *after* the change is the only source of
truth. It must be deterministic and FAITHFUL to the real pipeline outcome — the
brain must never freely phrase "done" for a change that was refused or rolled
back (the maintainer's original ask: "don't confirm something that wasn't done").

This renders that line directly from the ``set_config_value`` tool result, in
de/en/es (every supported language — CLAUDE.md "Runtime Output Language"). It is
separate from ``echo_confirmation.format_outcome`` (the two-turn confirm flow)
because the failure results here carry no ``PendingMutation``, only an
``error_kind``.

Pure dict lookups + value formatting — no LLM, no IO (AP-11).
"""
from __future__ import annotations

from typing import Any, Literal

from .echo_confirmation import is_sensitive_path

_SUPPORTED = ("de", "en", "es")
_DEFAULT = "de"

# outcome key -> {lang -> template}. ``{label}`` / ``{value}`` are filled for the
# success cases; the failure cases are deliberately generic (no path/value leak).
_PHRASES: dict[str, dict[str, str]] = {
    "applied": {
        "de": "Erledigt — {label} ist jetzt {value}.",
        "en": "Done — {label} is now {value}.",
        "es": "Listo — {label} ahora es {value}.",
    },
    "applied_restart": {
        "de": "Erledigt — {label} ist jetzt {value}. Starte Jarvis einmal neu, "
              "damit es wirkt.",
        "en": "Done — {label} is now {value}. Restart Jarvis once for it to take "
              "effect.",
        "es": "Listo — {label} ahora es {value}. Reinicia Jarvis una vez para que "
              "surta efecto.",
    },
    # Hard-refuse (secrets / self-lockout) — an honest decline, never a question.
    "refused": {
        "de": "Das ändere ich nicht per Stimme — das bitte bewusst in den "  # i18n-allow
              "Einstellungen.",  # i18n-allow
        "en": "I won't change that by voice — please do that deliberately in "
              "Settings.",
        "es": "Eso no lo cambio por voz — hazlo a propósito en los Ajustes.",
    },
    "unknown_setting": {
        "de": "Das ist keine Einstellung, die ich ändern kann.",  # i18n-allow
        "en": "That isn't a setting I can change.",
        "es": "Ese no es un ajuste que pueda cambiar.",
    },
    "invalid_value": {
        "de": "Das geht so nicht — die Einstellung bleibt unverändert.",  # i18n-allow
        "en": "That doesn't work — the setting stays unchanged.",
        "es": "Eso no funciona — el ajuste queda sin cambios.",
    },
    "rollback": {
        "de": "Konnte ich nicht speichern, ich habe den vorherigen Stand "  # i18n-allow
              "wiederhergestellt.",  # i18n-allow
        "en": "I couldn't save it, I restored the previous state.",
        "es": "No pude guardarlo, restauré el estado anterior.",
    },
}

# set_config_value error_kind -> outcome key.
_ERROR_KIND_TO_OUTCOME: dict[str, str] = {
    "forbidden_path": "refused",
    "path_not_allowed": "unknown_setting",
    "validate_failed": "invalid_value",
    "reload_failed_rolled_back": "rollback",
    "rollback_failed": "rollback",
}


def _lang(language: str | None) -> str:
    code = str(language or "").strip().lower()
    return code if code in _SUPPORTED else _DEFAULT


def _value_for_speech(value: Any, lang: str) -> str:
    """Render a setting value for speech (bool localized, else stringified)."""
    if isinstance(value, bool):  # before int — bool is an int subtype
        on = {"de": "an", "en": "on", "es": "activado"}[lang]
        off = {"de": "aus", "en": "off", "es": "desactivado"}[lang]
        return on if value else off
    if value is None:
        return {"de": "leer", "en": "empty", "es": "vacío"}[lang]
    return str(value)


def _label(description: str) -> str:
    """A short spoken label from the setting description (first clause)."""
    head = (description or "").split("(")[0].split("—")[0].strip(" .")
    return head or "the setting"


def config_readback(
    *, success: bool, output: Any, language: str | Literal["de", "en", "es"] = "de"
) -> str | None:
    """The honest spoken line for a ``set_config_value`` result, or ``None``.

    ``None`` means "not a recognizable config outcome" — the caller then keeps
    its normal (free-form brain) phrasing.
    """
    if not isinstance(output, dict):
        return None
    lang = _lang(language)

    # Success: ``output`` is a PendingMutation dump (carries ``applied``).
    if success and "applied" in output and output.get("applied") is True:
        sens = is_sensitive_path(str(output.get("path", "")))
        if sens:
            value = {"de": "der neue Wert", "en": "the new value",
                     "es": "el nuevo valor"}[lang]
        else:
            value = _value_for_speech(output.get("new_value"), lang)
        key = "applied_restart" if output.get("requires_restart") else "applied"
        return _PHRASES[key][lang].format(label=_label(str(output.get("description", ""))),
                                          value=value)

    # Failure: ``output`` carries ``error_kind``.
    outcome = _ERROR_KIND_TO_OUTCOME.get(str(output.get("error_kind", "")))
    if outcome is None:
        return None
    return _PHRASES[outcome][lang]


__all__ = ["config_readback"]
