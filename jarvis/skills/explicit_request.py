"""Deterministic resolution of "use the skill X" — the user named it.

The doctrine in :mod:`jarvis.skills.autofire_policy` reserves two sanctioned
paths for a dispatching-class skill: the model asks via ``run-skill``, or THE
USER NAMES IT. Before this module the second path was prompt-only — the
pc-control gate kept ``run-skill`` visible when the word "skill" appeared, and
everything after that was hope that a fast router model makes the call. Live
telemetry (14 days, 2026-08-12 rework) measured that hope at zero calls.

This resolver closes that gap deterministically, in the same spirit as the
author-regex trigger channel: pure CPU, no LLM, no IO (AP-9/AP-11 safe), and
precision over recall. It fires only when ALL THREE hold:

1. an explicit use-verb ("nutz", "starte", "run", "use", "ejecuta", …),
2. the literal word "skill(s)" — the user is talking about the mechanism,
3. an installed, active skill whose NAME is spoken in the utterance,
4. and the utterance is NOT an informational question ("how do I use the X
   skill?" must be answered, never executed — see the opener veto below).

"Welche skills habe ich?" has no use-verb; "erstell mir einen skill …" has no
resolvable installed name; "starte die morgenroutine" has no "skill" word and
stays with the trigger/relevance channels. All of these miss by design.

Name matching is speech-tolerant: kebab-case names are compared as normalized
token sequences ("morning-routine" ↔ "morning routine"), and the namespace
prefixes ``plugin-`` / ``cli-`` may be omitted ("use the gmail skill" resolves
``plugin-gmail``). The longest spoken name wins so "focus pro" never resolves
to a skill called "focus".
"""
from __future__ import annotations

import re
from typing import Any

from jarvis.skills.match_eval import (
    BAND_FIRE,
    SOURCE_TRIGGER,
    TRIGGER_MATCH_SCORE,
    MatchCandidate,
    MatchDecision,
)
from jarvis.skills.relevance import normalize_text

#: Imperative use-verbs in DE / EN / ES, matched against the RAW utterance
#: (umlaut and digraph forms both listed). Infinitives are included because
#: polite modal phrasing is how people actually speak ("kannst du den Skill X
#: nutzen"). Deliberately excludes authoring verbs ("erstell", "bau",
#: "create") — creating a skill is not running one.
_EXPLICIT_VERB_RE = re.compile(
    r"\b("
    r"nutz(?:e|t|en)?|benutz(?:e|t|en)?|verwend(?:e|et|en)?|"  # i18n-allow: speech-input vocabulary
    r"start(?:e|et|en)?|f[üu]hr(?:e|t|en)?|fuehr(?:e|t|en)?|"  # i18n-allow: speech-input vocabulary
    r"aktivier(?:e|t|en)?|"  # i18n-allow: speech-input vocabulary
    r"run|use|execute|launch|trigger|invoke|activate|start|"
    r"usa|ejecuta|lanza|activa"  # i18n-allow: speech-input vocabulary
    r")\b",
    re.IGNORECASE,
)

#: Informational question openers (DE / EN / ES), after an optional wake or
#: politeness run. A question ABOUT a skill — "how do I use the cloud-debug
#: skill?", or its German "what is it used for" form —  # i18n-allow: example
#: satisfies the verb + skill-word + name gates exactly like a command does,
#: yet running the skill would be the wrong answer: for a mission skill it
#: would silently dispatch a background worker off a question (review finding
#: 2026-08-12). Only interrogative INFORMATION openers veto; polite-modal
#: request forms ("kannst du …", "can you …") stay commands. Mirrors the
#: manager's ``_QUESTION_OPENER_RE`` shape.
_INFO_QUESTION_OPENER_RE = re.compile(
    r"^\s*(?:(?:hey|hallo|hi|hello|ok(?:ay)?|bitte|please|jarvis)[\s,]+){0,3}"
    r"(?:"
    r"was|wie|wieso|warum|weshalb|wann|wer|wen|wem|wessen|"  # i18n-allow: speech-input vocabulary
    r"welche[rsnm]?|wof[üu]r|wofuer|wozu|woher|wohin|wo|"  # i18n-allow: speech-input vocabulary
    r"how|what|whats|why|which|who|whom|whose|when|where|"
    r"qu[ée]|c[óo]mo|cu[áa]ndo|d[óo]nde|por\s+qu[ée]|"  # i18n-allow: speech-input vocabulary
    r"para\s+qu[ée]|qui[ée]n(?:es)?|cu[áa]l(?:es)?"  # i18n-allow: speech-input vocabulary
    r")\b",
    re.IGNORECASE,
)

#: The user must literally say "skill(s)" — same word DE/EN/ES. Mirrors the
#: pc-control gate's ``_EXPLICIT_SKILL_REQUEST_RE`` in the brain manager.
_SKILL_WORD_RE = re.compile(r"\bskills?\b", re.IGNORECASE)

#: Namespace prefixes a speaker naturally drops ("the gmail skill").
_DROPPABLE_NAME_PREFIXES = frozenset({"plugin", "cli"})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _name_token_sequences(name: str) -> list[tuple[str, ...]]:
    """Spoken token sequences under which a skill name is recognized."""
    tokens = tuple(t for t in _TOKEN_RE.findall(normalize_text(name)) if t)
    if not tokens:
        return []
    sequences = [tokens]
    if len(tokens) > 1 and tokens[0] in _DROPPABLE_NAME_PREFIXES:
        sequences.append(tokens[1:])
    return sequences


def _contains_sequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """True when ``needle`` appears as a CONTIGUOUS run inside ``haystack``."""
    if not needle or len(needle) > len(haystack):
        return False
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            return True
    return False


def resolve_explicit_skill_request(
    utterance: str, registry: Any
) -> tuple[Any, MatchDecision] | None:
    """Resolve "use the skill X" to the installed skill the user named.

    Returns ``(skill, decision)`` — the decision carries ``SOURCE_TRIGGER``
    on purpose: a spoken name is the same grade of evidence as an author's
    trigger phrase (the user stated intent verbatim), so it inherits the
    trigger channel's unconditional capture and its full stand-down rights.
    Returns ``None`` on any miss. Never raises.
    """
    if not utterance or registry is None:
        return None
    try:
        if not _SKILL_WORD_RE.search(utterance):
            return None
        if not _EXPLICIT_VERB_RE.search(utterance):
            return None
        if _INFO_QUESTION_OPENER_RE.match(utterance):
            # A question about the skill must be ANSWERED, never executed.
            return None
        spoken = tuple(_TOKEN_RE.findall(normalize_text(utterance)))
        if not spoken:
            return None

        best: tuple[int, str, Any] | None = None  # (match_len, name, skill)
        for skill in registry.list_active():
            name = str(getattr(skill, "name", "") or "")
            if not name:
                continue
            for sequence in _name_token_sequences(name):
                if not _contains_sequence(spoken, sequence):
                    continue
                # Longest spoken sequence wins; ties break on the name for
                # determinism regardless of registry iteration order.
                key = (len(sequence), name)
                if best is None or key > (best[0], best[1]):
                    best = (len(sequence), name, skill)
        if best is None:
            return None

        _, name, skill = best
        candidate = MatchCandidate(
            skill_name=name,
            score=TRIGGER_MATCH_SCORE,
            band=BAND_FIRE,
            source=SOURCE_TRIGGER,
            evidence=name,
            reason=f"user explicitly requested skill {name!r} by name",
        )
        decision = MatchDecision(
            band=BAND_FIRE,
            source=SOURCE_TRIGGER,
            top=candidate,
            candidates=(candidate,),
        )
        return skill, decision
    except Exception:  # noqa: BLE001 — resolution must never break a turn
        return None


__all__ = ["resolve_explicit_skill_request"]
