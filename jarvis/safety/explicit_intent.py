"""Deterministic check: did the USER explicitly ask for the destructive act?

The Claude-Code permission model, adapted for voice (Ruben's mandate,
2026-08-08): harmless commands run silently, destructive commands confirm —
UNLESS the user's own utterance already named the destruction ("lösch den
Ordner Urlaub"). Asking "do you really want me to delete?" right after the
user said "delete" is confirmation fatigue, the exact failure the whitelist
era was built to avoid. The flip side stays protected: when the brain decides
ON ITS OWN that deleting something is a useful step (the utterance never
mentioned it), the confirmation fires.

Deliberately dumb: a word-boundary vocabulary match, no LLM (AP-11 — the
safety path must be deterministic and fast). The vocabulary is speech-input
vocabulary in de/en/es (CLAUDE.md §1 — registered in the german-allowlist);
it covers DESTRUCTION verbs only. STT-confidence doubts are not this
module's job: a plausibility-forced confirmation is never skipped (see
ToolExecutor).
"""
from __future__ import annotations

import re

# Word stems that only appear when the speaker explicitly asks for deletion,
# formatting, or a machine power action. Stems (not full words) so German
# inflection ("lösch", "lösche", "löschst", "gelöscht") matches without a
# morphology engine; the \w* tail absorbs endings. Compiled once.
_DESTRUCTION_STEMS: tuple[str, ...] = (
    # German
    "lösch", "gelöscht", "entfern", "entfernt", "wegwerfen", "wegschmeiß",
    "papierkorb", "formatier", "runterfahren", "herunterfahren", "neustart",
    "neu starten",
    # English
    "delete", "remove", "erase", "wipe", "trash", "uninstall", "format",
    "shut down", "shutdown", "restart", "reboot", "power off",
    # Spanish
    "borra", "borrar", "elimina", "eliminar", "quita", "quitar", "formatea",
    "apaga", "apagar", "reinicia", "reiniciar",
)

_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(stem) + r"\w*" for stem in _DESTRUCTION_STEMS) + r")",
    re.IGNORECASE,
)


def utterance_confirms_destruction(utterance: str) -> bool:
    """True when the utterance itself explicitly asks for a destructive act.

    Empty/whitespace utterances (API calls, missions without a spoken turn)
    never confirm — the confirmation question stays the default there.
    """
    if not utterance or not utterance.strip():
        return False
    return bool(_PATTERN.search(utterance))
