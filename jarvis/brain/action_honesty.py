"""Execution-state backstop for model promises about future actions.

Models occasionally end a turn with an acknowledgement such as "I'll check and
get back to you" without emitting a tool call. Jarvis has no autonomous
continuation after that response, so the sentence is not harmless filler: it is
an ungrounded claim that work is running. This module detects that narrow,
high-confidence shape with regex only and provides a localized honest fallback.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


_ACTION_COMMITMENT_RE = re.compile(
    r"(?:"
    r"\b(?:das|es)\s+kann\s+ich(?:\s+gerne)?"  # i18n-allow: German output matcher
    r"(?:\s+f(?:u|ue)r\s+dich)?\s+"  # i18n-allow: German output matcher
    r"(?:nachschauen|nachsehen|pr(?:u|ue)fen|checken|"  # i18n-allow: German output matcher
    r"lesen|holen|(?:o|oe)ffnen|speichern)|"  # i18n-allow: German output matcher
    r"\bich\s+(?:werde\s+|werd\s+|will\s+|kann\s+)?"  # i18n-allow: German output matcher
    r"(?:schaue|schau|gucke|nachschauen|nachsehen|"  # i18n-allow: German output matcher
    r"pr(?:u|ue)fen|checken|nachforschen|recherchieren|"  # i18n-allow: German output matcher
    r"lesen|holen|(?:o|oe)ffnen|speichern|eintragen|"  # i18n-allow: German output matcher
    r"starten)|"  # i18n-allow: German output matcher
    r"\bich\s+werfe(?:\s+(?:kurz|mal))?\s+"  # i18n-allow: German output matcher
    r"einen?\s+blick|"  # i18n-allow: German output matcher
    r"\b(?:let\s+me|i(?:'ll|\s+will|'m\s+going\s+to|\s+am\s+going\s+to|\s+can))\s+"
    r"(?:look|check|review|read|fetch|open|save|enter|start|research|inspect)|"
    r"\b(?:voy\s+a|dejame)\s+"
    r"(?:mirar|revisar|consultar|leer|buscar|abrir|"  # i18n-allow: Spanish output matcher
    r"guardar|anotar|iniciar)"  # i18n-allow: Spanish output matcher
    r")"
)

_DEFER_MARKER_RE = re.compile(
    r"(?:"
    r"\b(?:einen?\s+moment|warte(?:\s+kurz)?|"  # i18n-allow: German output matcher
    r"gleich|sp(?:a|ae)ter|danach)\b|"  # i18n-allow: German output matcher
    r"\b(?:sage|melde)\s+(?:ich\s+)?dir\b|"  # i18n-allow: German output matcher
    r"\b(?:one\s+moment|give\s+me\s+(?:a|one)\s+moment|later|shortly)\b|"
    r"\b(?:get|come|report)\s+back\b|"
    r"\b(?:un\s+momento|espera|enseguida|luego|"  # i18n-allow: Spanish output matcher
    r"despues)\b|"  # i18n-allow: Spanish output matcher
    r"\b(?:te\s+digo|te\s+cuento|"  # i18n-allow: Spanish output matcher
    r"vuelvo\s+contigo)\b"  # i18n-allow: Spanish output matcher
    r")"
)

_GROUNDED_RESULT_RE = re.compile(
    r"(?:"
    r"\b(?:the\s+(?:answer|result)\s+is|i\s+(?:found|checked)\b|it\s+shows\b)|"
    r"\b(?:die\s+antwort\s+ist|das\s+ergebnis\s+ist|"  # i18n-allow: German output matcher
    r"ich\s+habe\s+(?:gefunden|nachgesehen|"  # i18n-allow: German output matcher
    r"nachgeschaut))\b|"  # i18n-allow: German output matcher
    r"\b(?:la\s+respuesta\s+es|el\s+resultado\s+es|"  # i18n-allow: Spanish output matcher
    r"he\s+encontrado)\b"  # i18n-allow: Spanish output matcher
    r")"
)

_ACTION_NOT_STARTED_PHRASES: dict[str, str] = {
    "de": (
        "Ich habe dafür gerade keine Aktion gestartet und deshalb noch kein "  # i18n-allow: runtime voice phrase
        "Ergebnis. Bitte sag es noch einmal."  # i18n-allow: runtime voice phrase
    ),  # i18n-allow: German runtime voice/chat output
    "en": (
        "I did not start an action for that, so I do not have a result yet. Please ask me again."
    ),
    "es": (
        "No inicié ninguna acción para eso, así que todavía no tengo un "
        "resultado. Pídemelo de nuevo."
    ),  # i18n-allow: Spanish runtime voice/chat output
}


def has_deferred_action_claim(text: str) -> bool:
    """Return whether ``text`` ends the turn on uncompleted future work."""
    normalized = _normalize(text).strip()
    if not normalized:
        return False
    match = _ACTION_COMMITMENT_RE.search(normalized)
    if match is None:
        return False

    remainder = normalized[match.end() :].strip()
    if _GROUNDED_RESULT_RE.search(remainder):
        return False
    if _DEFER_MARKER_RE.search(normalized):
        return True

    # A bare commitment with no delivered result is still terminal: after the
    # model response closes there is no hidden continuation that will do it.
    return len(remainder.strip(" .,!?:;-")) <= 24


def action_not_started_phrase(language: str) -> str:
    """Return the honest fallback in one resolved runtime output language."""
    return _ACTION_NOT_STARTED_PHRASES.get(
        str(language or "").strip().lower(),
        _ACTION_NOT_STARTED_PHRASES["en"],
    )


def replace_unbacked_action_claim(
    text: str,
    *,
    executed_tools: Collection[str],
    language: str,
) -> str:
    """Replace a deferred claim unless this turn has execution evidence."""
    if executed_tools or not has_deferred_action_claim(text):
        return text
    return action_not_started_phrase(language)


__all__ = [
    "action_not_started_phrase",
    "has_deferred_action_claim",
    "replace_unbacked_action_claim",
]
