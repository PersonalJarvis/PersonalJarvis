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
vocabulary in de/en/es (AGENTS.md §1 — registered in the german-allowlist);
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


# Handing a task to a coding agent (live voice session 2026-10-01 18:11): the
# user said "beauftrag einen Agenten im Workspace, das smoother zu machen",
# Jarvis asked "soll ich ihn starten?", and on the next request ("da soll sich
# ein anderer Agent drum kümmern, im selben Workspace") asked again — the
# maintainer's verdict: "das hättest du einfach loslaufen lassen können". The
# words below only appear when the speaker addresses agent work themselves:
# the vehicle ("Agent", "worker"), a workspace target ("Terminal", "Session",
# "T2"), a coding CLI by name, or a hand-off verb. A send the brain decides on
# while the user talks about something else still confirms.
_AGENT_WORK_STEMS: tuple[str, ...] = (
    # Shared by de/en/es: agent/Agenten/agente, delegier/delegate/delega.
    "agent", "subagent", "deleg", "terminal", "workspace", "coding",
    # German
    "beauftrag", "sitzung", "arbeitsbereich",
    # English
    "worker", "spawn", "session", "pane", "claude code", "codex",
    # Spanish
    "sesión", "sesion", "espacio de trabajo",
)

_AGENT_WORK_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(re.escape(stem) + r"\w*" for stem in _AGENT_WORK_STEMS)
    # Agentic-IDE call signs ("T2", "t 4").
    + r"|t\s?\d{1,2}\b"
    + r")",
    re.IGNORECASE,
)

# "nicht an den Agenten", "don't send it to the agent", "kein Terminal": a
# negation shortly before the target, inside the same clause, is a refusal,
# not an order. A comma or full stop ends its reach ("nicht so, lass das
# einen Agenten machen" still asks for an agent).
_NEGATION_BEFORE = re.compile(
    r"\b(?:nicht|kein\w*|nie|no|not|don't|dont|never|nada|ningún|ninguna)"
    r"(?:[^\w,.;!?]+\w+){0,4}[^\w,.;!?]*$",
    re.IGNORECASE,
)


def utterance_requests_agent_work(utterance: str) -> bool:
    """True when the utterance itself asks to hand work to a coding agent.

    Used by the workspace send hook: the user who just said "let an agent do
    it" is not asked "shall I start it?" again. A target preceded by a
    negation ("schick das nicht an den Agenten") does not count. Empty
    utterances (API calls, missions) never confirm.
    """
    if not utterance or not utterance.strip():
        return False
    for match in _AGENT_WORK_PATTERN.finditer(utterance):
        if not _NEGATION_BEFORE.search(utterance[: match.start()]):
            return True
    return False
