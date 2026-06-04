"""End-Focus-Templater + deterministischer Yes/No-Klassifikator (Phase 7.4).

Plan-§7.4 Echo-Frage-Templates (DE+EN), End-Focus-Prinzip: der NEUE WERT
steht in den letzten Tokens des Satzes, damit STT-Misshears beim User
sofort auffallen.

Defense-in-Depth gegen TTS-Secret-Leak (Plan-§AP-2):
Sensitive Pfade (FORBIDDEN_PATTERNS oder `MutableSpec.sensitive=True`)
verschweigen den Wert komplett — der Echo-Satz endet bei „auf einen
neuen Wert. Bestätigen?" statt den Klartext zu sprechen.

Pattern-Match-Klassifikator (Plan-§AP-12): kein LLM-Call. Veto hat
Priorität vor Confirm (Sicherheits-Bias). Mehrdeutiges (`vielleicht`,
`warte`) bleibt `ambiguous` — die State-Machine wartet bis Timeout.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from jarvis.core.self_mod import (
    PendingMutation,
    SelfModRegistry,
)
from jarvis.core.self_mod.errors import (
    PreValidateError,
    ReloadError,
    RollbackError,
)

# ----------------------------------------------------------------------
# Pattern-Lists
# ----------------------------------------------------------------------

# Plan-§7.4 + Prompt-Erweiterung. Veto wird als Wort-Grenze-regex
# geprüft, damit „nichtig" (Zustimmung in Süddeutsch — selten) nicht
# fälschlich als Veto greift. Das `nicht`-Pattern ist absichtlich strikt.
_CONFIRM_PATTERNS_DE: tuple[str, ...] = (
    r"\bja\b",
    r"\bbest(ä|ae)tig(e|en|t)?\b",
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
    r"\bdoch nicht\b",
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
    r"\b(öh|öhm|äh|ähm|ehm|hmm|hm)\b",
    r"\bwei(ß|ss) (ich )?nicht\b",
    r"\bunklar\b",
)

_AMBIGUOUS_PATTERNS_EN: tuple[str, ...] = (
    r"\bmaybe\b",
    r"\bwait\b",
    r"\bnot sure\b",
    r"\bidk\b",
    r"\b(uh|uhm|hmm|hm)\b",
)

ResponseVerdict = Literal["confirm", "veto", "ambiguous", "unknown"]


def classify_response(transcript: str, *, language: str = "de") -> ResponseVerdict:
    """Klassifiziert die User-Antwort deterministisch (kein LLM-Call).

    Reihenfolge: Veto > Confirm > Ambiguous > Unknown.
    Veto-Priorität ist eine **Sicherheits-Eigenschaft** (Plan-§AP-12):
    bei „nein, doch ja" wird das `nein` ernst genommen, nicht ignoriert.
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
# Sensitive-Path-Check
# ----------------------------------------------------------------------


def is_sensitive_path(path: str) -> bool:
    """True wenn der Pfad einen Wert enthält, der NICHT vorgelesen werden darf.

    Drei Quellen:
    1. `SelfModRegistry.is_forbidden(path)` (FORBIDDEN_PATTERNS)
    2. `MutableSpec.sensitive == True` (explizit gesetzt)
    3. Heuristik: Pfad enthält key/secret/token/password als Substring
    """
    if SelfModRegistry.is_forbidden(path):
        return True
    spec = SelfModRegistry.get_spec(path)
    if spec is not None and spec.sensitive:
        return True
    # Defense-in-Depth: Heuristik gegen versehentliche Allowlist-Erweiterungen.
    # Sub-Agent-Review-MINOR (2026-04-26): Marker-Liste um `bearer`, `oauth`,
    # `pat` (Personal Access Token), `cookie`, `session_id` erweitert.
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
        # `pat` als ganzes Wort (Github-PAT), nicht als Substring von z.B. "patch".
        re.search(rf"(?:^|[._-]){marker}(?:$|[._-])", lowered)
        for marker in ("pat",)
    )


# ----------------------------------------------------------------------
# Voice-Label-Extraktion
# ----------------------------------------------------------------------


def _voice_label(description: str) -> str:
    """Voice-tauglicher Label aus der Description.

    Schneidet alles ab der ersten Klammer ab: „TTS-Provider (Hot-Reload
    abgedeckt)" → „TTS-Provider". Trailing-Punctuation wird ebenfalls
    entfernt, damit der Templater eine saubere Phrase einsetzen kann.
    """
    head = description.split("(")[0].strip()
    return head.rstrip(",;:.!?")


def _format_value_for_speech(value: Any) -> str:
    """Voice-tauglicher String aus einem primitiven Wert.

    Phase 7.4 hält das einfach — Number-Wörterung („eins Komma drei")
    ist Phase 7.6 (TTS macht das idealerweise selbst). Hier: stringify.
    """
    if isinstance(value, bool):
        # bool muss VOR int geprüft werden (bool ist int-Subtype).
        return "an" if value else "aus"
    if value is None:
        return "leer"
    return str(value)


# ----------------------------------------------------------------------
# Echo-Frage-Templates (Plan-§7.4 End-Focus)
# ----------------------------------------------------------------------


_ECHO_TEMPLATE_DE = (
    "Verstanden — {label} wechselt von {old} zu {new}. Bestätigen?"
)
_ECHO_TEMPLATE_EN = (
    "Got it — {label} switches from {old} to {new}. Confirm?"
)
_ECHO_TEMPLATE_SENSITIVE_DE = (
    "Verstanden — {label} auf einen neuen Wert. Bestätigen?"
)
_ECHO_TEMPLATE_SENSITIVE_EN = (
    "Got it — {label} to a new value. Confirm?"
)


def format_confirmation(
    pending: PendingMutation, *, language: str = "de"
) -> str:
    """Rendert die Echo-Frage gemäß Plan-§7.4 + Sensitive-Schutz.

    End-Focus: der `new_value` steht in den letzten 3 Tokens des Satzes
    (vor `Bestätigen?`/`Confirm?`). Bei Sensitive-Pfaden wird der Wert
    komplett ausgelassen — Defense-in-Depth gegen TTS-Secret-Leak.
    """
    label = _voice_label(pending.description)
    if is_sensitive_path(pending.path):
        tmpl = _ECHO_TEMPLATE_SENSITIVE_DE if language == "de" else _ECHO_TEMPLATE_SENSITIVE_EN
        return tmpl.format(label=label)
    tmpl = _ECHO_TEMPLATE_DE if language == "de" else _ECHO_TEMPLATE_EN
    return tmpl.format(
        label=label,
        old=_format_value_for_speech(pending.old_value),
        new=_format_value_for_speech(pending.new_value),
    )


# ----------------------------------------------------------------------
# Outcome-Templates (Plan-§7.4-Tabelle)
# ----------------------------------------------------------------------


OutcomeKind = Literal[
    "safe_applied",  # SAFE-Tier-Bypass
    "applied",       # SUCCESS, kein Restart
    "applied_restart",  # SUCCESS, mit Restart
    "validate_failed",  # PRE-VALIDATION-FAIL
    "rollback",      # ROLLBACK
    "vetoed",        # REJECT
    "timeout",       # TIMEOUT
]


def format_outcome(
    kind: OutcomeKind,
    pending: PendingMutation,
    *,
    language: str = "de",
    short_error: str | None = None,
) -> str:
    """Plan-§7.4 Voice-Output-Templates.

    Sub-Agent-Review-BLOCKER (2026-04-26): bei `is_sensitive_path == True`
    werden NICHT NUR `new_value`/`old_value`, sondern AUCH `short_error`
    durch eine generische Phrase ersetzt — eine Pydantic-Pre-Validate-
    Message kann den Klartext-Wert via `repr()` enthalten und würde
    sonst leaken (Plan-§AP-2).
    """
    label = _voice_label(pending.description)
    is_sens = is_sensitive_path(pending.path)
    new_str = (
        "der neue Wert" if (is_sens and language == "de")
        else "the new value" if (is_sens and language == "en")
        else _format_value_for_speech(pending.new_value)
    )
    old_str = (
        "der vorherige Wert" if (is_sens and language == "de")
        else "the previous value" if (is_sens and language == "en")
        else _format_value_for_speech(pending.old_value)
    )
    if is_sens:
        # Generische Phrase — kein Klartext-Leak via Exception-Message.
        err = (
            "Validierung schlug fehl"
            if language == "de"
            else "validation failed"
        )
    else:
        err = short_error or (
            "unbekannter Fehler" if language == "de" else "unknown error"
        )

    if language == "de":
        if kind == "safe_applied":
            return f"Geht klar — {label} jetzt {new_str}."
        if kind == "applied":
            return f"Erledigt — {label} ist jetzt {new_str}."
        if kind == "applied_restart":
            return (
                f"Erledigt — {label} ist jetzt {new_str}. "
                "Bitte einmal Jarvis neustarten, damit's wirkt."
            )
        if kind == "validate_failed":
            return f"Geht nicht — {err}. Setting bleibt {old_str}."
        if kind == "rollback":
            return (
                "Konnte nicht gespeichert werden, hab den vorherigen "
                f"Zustand wiederhergestellt. {err}"
            )
        if kind == "vetoed":
            return "Okay, lass ich."
        if kind == "timeout":
            return f"Hab keine Antwort gehört, brech ich ab. Setting bleibt {old_str}."
        return ""

    # English
    if kind == "safe_applied":
        return f"Got it — {label} is now {new_str}."
    if kind == "applied":
        return f"Done — {label} is now {new_str}."
    if kind == "applied_restart":
        return (
            f"Done — {label} is now {new_str}. "
            "Please restart Jarvis for the change to take effect."
        )
    if kind == "validate_failed":
        return f"Can't do that — {err}. Setting stays {old_str}."
    if kind == "rollback":
        return (
            "Couldn't save it, reverted to the previous state. " + err
        )
    if kind == "vetoed":
        return "Okay, leaving it."
    if kind == "timeout":
        return f"No answer heard, aborting. Setting stays {old_str}."
    return ""


def short_error_from_exception(exc: BaseException) -> str:
    """Kürzt eine Pipeline-Exception auf eine Voice-taugliche Phrase.

    Sub-Agent-Review-MAJOR (2026-04-26): der Fallback-Branch reichte
    früher beliebige Exception-Strings durch — wenn ein zukünftiger Caller
    den Catch-Block weitet (z.B. `except Exception`), würde der Fallback
    Klartext-Werte ins TTS leiten. Jetzt: nur eine harte Allowlist von
    Phrasen, plus Pydantic-Field-Path (kein Wert) für PreValidateError.
    """
    if isinstance(exc, PreValidateError):
        # `str(exc)` enthält in writer.py:_mutate_locked den `repr(new_value)`.
        # Wir verwerfen die Message komplett und liefern eine generische
        # Phrase. Der detaillierte Trace landet im Audit-Log, nicht im TTS.
        return "Pre-Validate hat den Wert abgelehnt"
    if isinstance(exc, ReloadError):
        return "der Reload-Test hat den neuen Wert abgelehnt"
    if isinstance(exc, RollbackError):
        return "der Rollback selbst ist fehlgeschlagen — manueller Restore nötig"
    return "unerwarteter Fehler in der Mutation-Pipeline"
