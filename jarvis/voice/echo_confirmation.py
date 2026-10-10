"""End-focus templater + deterministic yes/no classifier (Phase 7.4).

Plan-§7.4 echo-question templates (DE+EN), end-focus principle: the NEW
VALUE sits in the last tokens of the sentence, so STT mis-hears jump out to
the user immediately.

Defense-in-depth against a TTS secret leak (Plan-§AP-2):
sensitive paths (FORBIDDEN_PATTERNS or `MutableSpec.sensitive=True`)
withhold the value entirely — the echo sentence ends with "a
new value. Confirm?" instead of speaking the plain-text value.

Pattern-match classifier (Plan-§AP-12): no LLM call. Veto takes
priority over confirm (safety bias). Ambiguous input (hedging words
like "maybe", "wait") stays `ambiguous` — the state machine waits until timeout.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from jarvis.core.self_mod import PendingMutation
from jarvis.core.self_mod.errors import (
    PreValidateError,
    ReloadError,
    RollbackError,
)

# ----------------------------------------------------------------------
# Pattern lists
# ----------------------------------------------------------------------

# Plan-§7.4 + prompt extension. Veto is checked via a word-boundary
# regex, so that "nichtig" (an affirmative in southern German dialect — rare) doesn't  # i18n-allow
# falsely trigger as a veto. The `nicht` pattern is deliberately strict.  # i18n-allow
_CONFIRM_PATTERNS_DE: tuple[str, ...] = (
    r"\bja\b",
    r"\bbest(ä|ae)tig(e|en|t)?\b",  # i18n-allow
    r"\bmach\b",
    r"\bmach('s| es)\b",
    r"\blos\b",
    r"\bokay\b",
    r"\bok\b",
    r"\bpasst\b",
    r"\bstimmt\b",
    r"\bkorrekt\b",
    r"\bgenau\b",
    r"\brichtig\b",
)

_CONFIRM_PATTERNS_EN: tuple[str, ...] = (
    r"\byes\b",
    r"\bconfirm(ed|s)?\b",
    r"\bdo it\b",
    r"\bcorrect\b",
    r"\bsure\b",
    r"\bgo ahead\b",
    r"\bgo for it\b",
    r"\baffirmative\b",
)

_VETO_PATTERNS_DE: tuple[str, ...] = (
    r"\bnein\b",
    r"\babbreche?n?\b",
    r"\bstop+\b",
    r"\babbruch\b",
    r"\bnicht\b",
    r"\bdoch nicht\b",  # i18n-allow
    r"\blass\b",
    r"\blass das\b",
    r"\bfalsch\b",
)

_VETO_PATTERNS_EN: tuple[str, ...] = (
    r"\bno\b",
    r"\bcancel\b",
    r"\babort\b",
    r"\bwrong\b",
    r"\bnever ?mind\b",
    r"\bstop\b",
)

_AMBIGUOUS_PATTERNS_DE: tuple[str, ...] = (
    r"\bvielleicht\b",
    r"\bwarte\b",
    r"\bmoment\b",
    r"\b(öh|öhm|äh|ähm|ehm|hmm|hm)\b",  # i18n-allow
    r"\bwei(ß|ss) (ich )?nicht\b",  # i18n-allow
    r"\bunklar\b",
)

_AMBIGUOUS_PATTERNS_EN: tuple[str, ...] = (
    r"\bmaybe\b",
    r"\bwait\b",
    r"\bnot sure\b",
    r"\bidk\b",
    r"\b(uh|uhm|hmm|hm)\b",
)

# Spanish (Runtime Output Language doctrine: the classifier must cover es too —
# an es-pinned user could not confirm/veto by voice before this; "es" silently
# used the German patterns). Veto keeps priority over confirm (safety bias,
# Plan-§AP-12). NB: "para" is deliberately NOT a veto keyword — it is also the
# common preposition ("para enviar" = "to send"), so it would cause false vetoes;
# unambiguous stop words are used instead.
_CONFIRM_PATTERNS_ES: tuple[str, ...] = (
    r"\bs[íi]\b",
    r"\bvale\b",
    r"\bclaro\b",
    r"\bhazlo\b",
    r"\bhaz\b",
    r"\bcorrecto\b",
    r"\bde acuerdo\b",
    r"\bacuerdo\b",
    r"\badelante\b",
    r"\bdale\b",
    r"\bconfirm(o|ar|ado)?\b",
    r"\bperfecto\b",
    r"\bexacto\b",
)

_VETO_PATTERNS_ES: tuple[str, ...] = (
    r"\bno\b",
    r"\bcancela(r)?\b",
    r"\babortar?\b",
    r"\bdet(é|e)n(te)?\b",
    r"\bd(é|e)jalo\b",
    r"\bolv[ií]da(lo)?\b",
    r"\balto\b",
    r"\bbasta\b",
    r"\bmal\b",
    r"\bincorrecto\b",
    r"\bnegativo\b",
)

_AMBIGUOUS_PATTERNS_ES: tuple[str, ...] = (
    r"\bquiz[aá]s?\b",
    r"\btal vez\b",
    r"\bespera\b",
    r"\bmomento\b",
    r"\bni idea\b",
)

# European Portuguese (Runtime Output Language doctrine: every supported
# language gets its own patterns). As in Spanish, "para" is NOT a veto keyword:
# it is the common preposition ("para enviar" = "to send"). "não sei" needs no
# ambiguous entry — the veto pass sees "não" first (safety bias, Plan-§AP-12).
_CONFIRM_PATTERNS_PT: tuple[str, ...] = (
    r"\bsim\b",
    r"\bclaro\b",
    r"\bpode ser\b",
    r"\bpodes\b",
    r"\bfor(ç|c)a\b",
    r"\bfaz\b",
    r"\bavan(ç|c)a\b",
    r"\bok\b",
    r"\bokay\b",
    r"\bcerto\b",
    r"\bcorreto\b",
    r"\bexato\b",
    r"\bperfeito\b",
    r"\bde acordo\b",
    r"\bconfirm(o|ar|ado)?\b",
    r"\bv(á|a) l(á|a)\b",
)

_VETO_PATTERNS_PT: tuple[str, ...] = (
    r"\bn(ã|a)o\b",
    r"\bcancela(r)?\b",
    r"\baborta(r)?\b",
    r"\bp(á|a)ra com isso\b",
    r"\bdeixa estar\b",
    r"\bdeixa l(á|a)\b",
    r"\besquece\b",
    r"\bnem pensar\b",
    r"\balto\b",
    r"\bbasta\b",
    r"\berrado\b",
    r"\bincorreto\b",
    r"\bnegativo\b",
)

_AMBIGUOUS_PATTERNS_PT: tuple[str, ...] = (
    r"\btalvez\b",
    r"\bse calhar\b",
    r"\bespera\b",
    r"\bmomento\b",
    r"\bsei l(á|a)\b",
    r"\b(hum|hmm|hm|eh)\b",
)

ResponseVerdict = Literal["confirm", "veto", "ambiguous", "unknown"]


def classify_response(transcript: str, *, language: str = "de") -> ResponseVerdict:
    """Deterministically classifies the user's answer (no LLM call).

    Order: Veto > Confirm > Ambiguous > Unknown.
    Veto priority is a **safety property** (Plan-§AP-12): for input like
    "no, actually yes" the negation is taken seriously, not ignored.
    """
    if not transcript:
        return "unknown"
    norm = transcript.lower().strip()
    if not norm:
        return "unknown"

    if language == "en":
        veto_pats = _VETO_PATTERNS_EN
        confirm_pats = _CONFIRM_PATTERNS_EN
        ambig_pats = _AMBIGUOUS_PATTERNS_EN
    elif language == "es":
        veto_pats = _VETO_PATTERNS_ES
        confirm_pats = _CONFIRM_PATTERNS_ES
        ambig_pats = _AMBIGUOUS_PATTERNS_ES
    elif language == "pt":
        veto_pats = _VETO_PATTERNS_PT
        confirm_pats = _CONFIRM_PATTERNS_PT
        ambig_pats = _AMBIGUOUS_PATTERNS_PT
    else:
        veto_pats = _VETO_PATTERNS_DE
        confirm_pats = _CONFIRM_PATTERNS_DE
        ambig_pats = _AMBIGUOUS_PATTERNS_DE

    for pat in veto_pats:
        if re.search(pat, norm):
            return "veto"
    for pat in confirm_pats:
        if re.search(pat, norm):
            return "confirm"
    for pat in ambig_pats:
        if re.search(pat, norm):
            return "ambiguous"
    return "unknown"


# ----------------------------------------------------------------------
# Sensitive path check
# ----------------------------------------------------------------------


def is_sensitive_path(path: str) -> bool:
    """True if the path holds a value that must NOT be read aloud.

    Three sources:
    1. `SelfModRegistry.is_forbidden(path)` (FORBIDDEN_PATTERNS)
    2. `MutableSpec.sensitive == True` (explicitly set)
    3. Heuristic: path contains key/secret/token/password as a substring
    """
    from jarvis.core.self_mod.registry import SelfModRegistry

    if SelfModRegistry.is_forbidden(path):
        return True
    spec = SelfModRegistry.get_spec(path)
    if spec is not None and spec.sensitive:
        return True
    # Defense-in-depth: heuristic against accidental allowlist expansions.
    # Sub-agent-review-MINOR (2026-04-26): marker list expanded with `bearer`,
    # `oauth`, `pat` (Personal Access Token), `cookie`, `session_id`.
    lowered = path.lower()
    return any(
        marker in lowered
        for marker in (
            "api_key",
            "api-key",
            "password",
            "passwd",
            "token",
            "secret",
            "credential",
            "bearer",
            "oauth",
            "session_id",
            "session-id",
            "cookie",
        )
    ) or any(
        # `pat` as a whole word (GitHub PAT), not as a substring of e.g. "patch".
        re.search(rf"(?:^|[._-]){marker}(?:$|[._-])", lowered)
        for marker in ("pat",)
    )


# ----------------------------------------------------------------------
# Voice label extraction
# ----------------------------------------------------------------------


def _voice_label(description: str) -> str:
    """Voice-friendly label derived from the description.

    Cuts everything from the first parenthesis: "TTS provider (hot-reload
    covered)" → "TTS provider". Trailing punctuation is also stripped, so
    the templater can slot in a clean phrase.
    """
    head = description.split("(")[0].strip()
    return head.rstrip(",;:.!?")


# Spoken words for primitive values, per supported reply language. An
# unknown language code speaks English (AP-21), never German.
_VALUE_WORDS: dict[str, dict[str, str]] = {
    "de": {"on": "an", "off": "aus", "empty": "leer"},  # i18n-allow
    "en": {"on": "on", "off": "off", "empty": "empty"},
    "es": {"on": "activado", "off": "desactivado", "empty": "vacío"},  # i18n-allow
    "pt": {"on": "ligado", "off": "desligado", "empty": "vazio"},  # i18n-allow
}


def _lang_key(language: str, table: dict[str, Any]) -> str:
    """*language* when the table carries it, else English."""
    return language if language in table else "en"


def _format_value_for_speech(value: Any, language: str = "de") -> str:
    """Voice-friendly string from a primitive value.

    Phase 7.4 keeps this simple — number verbalization ("one point three")
    is Phase 7.6 (ideally TTS handles that itself). Here: stringify.
    """
    words = _VALUE_WORDS[_lang_key(language, _VALUE_WORDS)]
    if isinstance(value, bool):
        # bool must be checked BEFORE int (bool is an int subtype).
        return words["on"] if value else words["off"]
    if value is None:
        return words["empty"]
    return str(value)


# ----------------------------------------------------------------------
# Echo question templates (Plan-§7.4 end-focus)
# ----------------------------------------------------------------------


_ECHO_TEMPLATES: dict[str, str] = {
    "de": "Verstanden — {label} wechselt von {old} zu {new}. Bestätigen?",  # i18n-allow
    "en": "Got it — {label} switches from {old} to {new}. Confirm?",
    "es": "Entendido — {label} cambia de {old} a {new}. ¿Confirmas?",  # i18n-allow
    "pt": "Entendido — {label} muda de {old} para {new}. Confirmas?",  # i18n-allow
}
_ECHO_TEMPLATES_SENSITIVE: dict[str, str] = {
    "de": "Verstanden — {label} auf einen neuen Wert. Bestätigen?",  # i18n-allow
    "en": "Got it — {label} to a new value. Confirm?",
    "es": "Entendido — {label} a un valor nuevo. ¿Confirmas?",  # i18n-allow
    "pt": "Entendido — {label} para um valor novo. Confirmas?",  # i18n-allow
}


def format_confirmation(
    pending: PendingMutation, *, language: str = "de"
) -> str:
    """Renders the echo question per Plan-§7.4 + sensitive protection.

    End-focus: the `new_value` sits in the last 3 tokens of the sentence
    (right before the localized "Confirm?" word). For sensitive paths, the
    value is omitted entirely — defense-in-depth against a TTS secret leak.
    """
    label = _voice_label(pending.description)
    lang = _lang_key(language, _ECHO_TEMPLATES)
    if is_sensitive_path(pending.path):
        return _ECHO_TEMPLATES_SENSITIVE[lang].format(label=label)
    return _ECHO_TEMPLATES[lang].format(
        label=label,
        old=_format_value_for_speech(pending.old_value, lang),
        new=_format_value_for_speech(pending.new_value, lang),
    )


# ----------------------------------------------------------------------
# Outcome templates (Plan-§7.4 table)
# ----------------------------------------------------------------------


OutcomeKind = Literal[
    "safe_applied",  # SAFE tier bypass
    "applied",       # SUCCESS, no restart
    "applied_restart",  # SUCCESS, with restart
    "validate_failed",  # PRE-VALIDATION FAIL
    "rollback",      # ROLLBACK
    "vetoed",        # REJECT
    "timeout",       # TIMEOUT
]

# Generic stand-ins for sensitive values / errors (no plaintext leak).
_OUTCOME_WORDS: dict[str, dict[str, str]] = {
    "de": {
        "new_value": "der neue Wert",  # i18n-allow
        "old_value": "der vorherige Wert",  # i18n-allow
        "validation_failed": "Validierung schlug fehl",  # i18n-allow
        "unknown_error": "unbekannter Fehler",  # i18n-allow
    },
    "en": {
        "new_value": "the new value",
        "old_value": "the previous value",
        "validation_failed": "validation failed",
        "unknown_error": "unknown error",
    },
    "es": {
        "new_value": "el valor nuevo",  # i18n-allow
        "old_value": "el valor anterior",  # i18n-allow
        "validation_failed": "la validación falló",  # i18n-allow
        "unknown_error": "error desconocido",  # i18n-allow
    },
    "pt": {
        "new_value": "o valor novo",  # i18n-allow
        "old_value": "o valor anterior",  # i18n-allow
        "validation_failed": "a validação falhou",  # i18n-allow
        "unknown_error": "erro desconhecido",  # i18n-allow
    },
}

# Outcome sentences; placeholders {label}, {new}, {old}, {err}.
_OUTCOME_TEMPLATES: dict[str, dict[str, str]] = {
    "de": {
        "safe_applied": "Geht klar — {label} jetzt {new}.",  # i18n-allow
        "applied": "Erledigt — {label} ist jetzt {new}.",  # i18n-allow
        "applied_restart": (
            "Erledigt — {label} ist jetzt {new}. "  # i18n-allow
            "Bitte einmal Jarvis neustarten, damit's wirkt."  # i18n-allow
        ),
        "validate_failed": "Geht nicht — {err}. Setting bleibt {old}.",  # i18n-allow
        "rollback": (
            "Konnte nicht gespeichert werden, hab den vorherigen "  # i18n-allow
            "Zustand wiederhergestellt. {err}"  # i18n-allow
        ),
        "vetoed": "Okay, lass ich.",  # i18n-allow
        "timeout": (
            "Hab keine Antwort gehört, brech ich ab. Setting bleibt {old}."  # i18n-allow
        ),
    },
    "en": {
        "safe_applied": "Got it — {label} is now {new}.",
        "applied": "Done — {label} is now {new}.",
        "applied_restart": (
            "Done — {label} is now {new}. "
            "Please restart Jarvis for the change to take effect."
        ),
        "validate_failed": "Can't do that — {err}. Setting stays {old}.",
        "rollback": "Couldn't save it, reverted to the previous state. {err}",
        "vetoed": "Okay, leaving it.",
        "timeout": "No answer heard, aborting. Setting stays {old}.",
    },
    "es": {
        "safe_applied": "Hecho — {label} ahora es {new}.",  # i18n-allow
        "applied": "Listo — {label} ahora es {new}.",  # i18n-allow
        "applied_restart": (
            "Listo — {label} ahora es {new}. "  # i18n-allow
            "Reinicia Jarvis para que el cambio surta efecto."  # i18n-allow
        ),
        "validate_failed": "No se puede — {err}. El ajuste sigue en {old}.",  # i18n-allow
        "rollback": (
            "No se pudo guardar, he restaurado el estado anterior. {err}"  # i18n-allow
        ),
        "vetoed": "Vale, lo dejo.",  # i18n-allow
        "timeout": (
            "No he oído respuesta, lo cancelo. El ajuste sigue en {old}."  # i18n-allow
        ),
    },
    "pt": {
        "safe_applied": "Feito — {label} agora é {new}.",  # i18n-allow
        "applied": "Pronto — {label} agora é {new}.",  # i18n-allow
        "applied_restart": (
            "Pronto — {label} agora é {new}. "  # i18n-allow
            "Reinicia o Jarvis para a alteração ter efeito."  # i18n-allow
        ),
        "validate_failed": "Não dá — {err}. A definição fica em {old}.",  # i18n-allow
        "rollback": (
            "Não consegui guardar, repus o estado anterior. {err}"  # i18n-allow
        ),
        "vetoed": "Está bem, deixo estar.",  # i18n-allow
        "timeout": (
            "Não ouvi resposta, vou cancelar. A definição fica em {old}."  # i18n-allow
        ),
    },
}


def format_outcome(
    kind: OutcomeKind,
    pending: PendingMutation,
    *,
    language: str = "de",
    short_error: str | None = None,
) -> str:
    """Plan-§7.4 voice-output templates.

    Sub-agent-review-BLOCKER (2026-04-26): when `is_sensitive_path == True`,
    NOT ONLY `new_value`/`old_value` but ALSO `short_error` is
    replaced with a generic phrase — a Pydantic pre-validate
    message can contain the plaintext value via `repr()` and would
    otherwise leak it (Plan-§AP-2).
    """
    lang = _lang_key(language, _OUTCOME_TEMPLATES)
    words = _OUTCOME_WORDS[lang]
    label = _voice_label(pending.description)
    is_sens = is_sensitive_path(pending.path)
    if is_sens:
        new_str = words["new_value"]
        old_str = words["old_value"]
        # Generic phrase — no plaintext leak via the exception message.
        err = words["validation_failed"]
    else:
        new_str = _format_value_for_speech(pending.new_value, lang)
        old_str = _format_value_for_speech(pending.old_value, lang)
        err = short_error or words["unknown_error"]
    template = _OUTCOME_TEMPLATES[lang].get(kind, "")
    return template.format(label=label, new=new_str, old=old_str, err=err)


def short_error_from_exception(exc: BaseException) -> str:
    """Shortens a pipeline exception to a voice-friendly phrase.

    Sub-agent-review-MAJOR (2026-04-26): the fallback branch used to pass
    arbitrary exception strings through — if a future caller
    widens the catch block (e.g. `except Exception`), the fallback
    would leak plaintext values into TTS. Now: only a hard allowlist of
    phrases, plus the Pydantic field path (no value) for PreValidateError.
    """
    if isinstance(exc, PreValidateError):
        # `str(exc)` contains `repr(new_value)` in writer.py:_mutate_locked.
        # We discard the message entirely and return a generic
        # phrase. The detailed trace lands in the audit log, not in the TTS.
        return "Pre-Validate hat den Wert abgelehnt"  # i18n-allow
    if isinstance(exc, ReloadError):
        return "der Reload-Test hat den neuen Wert abgelehnt"  # i18n-allow
    if isinstance(exc, RollbackError):
        return "der Rollback selbst ist fehlgeschlagen — manueller Restore nötig"  # i18n-allow
    return "unerwarteter Fehler in der Mutation-Pipeline"  # i18n-allow
