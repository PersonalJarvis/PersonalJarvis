"""Deterministic resolution of requests ABOUT skills — create one, or manage one.

Live failure this module exists for (voice session 2026-08-18 17:51): the
user asked, in German, for a new "morning routine" skill — read the mail,
tickets and calendar every morning at six, "… and then a nice song on YouTube
Music …" (the full transcript is the ``LIVE_UTTERANCE`` fixture in
``tests/unit/skills/test_authoring_request.py``).

The trigger channel gave that turn to ``plugin-youtube_music`` — its brand-only
trigger ``(youtube ?music|…)`` matched the words INSIDE the description of the
skill the user wanted built. The music skill's instructions were injected, the
model spent the whole 45 s tool budget reading ``jarvisctl --help`` and the
turn ended with the spoken "that did not work just now". The ``skill-creator``
builtin's own trigger missed because it only knew the German infinitive
("… Skill erstellen"), not the conjugated verb the user actually said
("… Skill erstellst").

The rule this resolver states is simple and holds regardless of vocabulary:
**when the user talks ABOUT a skill — asks for a new one, or asks to switch
one off, delete it, list them — every service they name is the CONTENT of that
request, never a command to that service.** A brand mentioned inside such a
request must not capture the turn.

Two kinds of request, same spirit as :mod:`jarvis.skills.explicit_request` —
pure CPU, no LLM, no IO (AP-9/AP-11 safe), precision over recall:

**Authoring** — the user wants a NEW skill. Fires only when ALL hold:

1. an internal-artifact noun — the literal word "skill(s)", or one of its
   spoken synonyms in this product: routine (incl. compounds such as
   "morning routine" as one word), automation, workflow, and their German
   and Spanish forms (see ``_ARTIFACT_NOUN``),
2. the noun is the OBJECT of a creation, in one of three shapes:
   an indefinite / "new" article before it ("einen neuen Skill", "a skill",
   "eine Abendroutine"), a creation verb right after it in any spoken
   conjugation ("Skill erstellst", "skill zu erstellen"), or a creation verb
   right before it ("erstell skill morgenroutine", "create skill …"),
3. the utterance is NOT an informational question ("wie erstelle ich einen
   skill?" must be answered, never acted on).

**Lifecycle** — the user wants an existing skill switched off / on, deleted,
listed or shown. Fires only when the literal word "skill(s)" is present
together with a lifecycle verb (EN "disable", "delete", "remove", "show",
"list" and their German / Spanish stems in ``_LIFECYCLE_VERB_RE``), and the
utterance is not an information question. The
`explicit-request` resolver ("nutz / starte / aktiviere den Skill X" on an
ACTIVE named skill) runs BEFORE this one in the manager and keeps its rights;
this kind covers what that resolver cannot — a draft to enable, a skill to
delete, the list — and hands the turn to the app-command tools
(``skill-enable``, ``skill-disable``, ``skill-delete``, ``skills-list``).

"use skill X" has no creation shape and no lifecycle verb (that is the
explicit-request resolver's turn), "switch the light off" has no skill word,
"which skills do I have" is a question (answered, not acted on), "make me an
overview of my skills" names the skills only in the genitive, "write me a
mail" has no skill word — in every language the resolver knows. All of these
miss by design (see the hard negatives in the tests).

Resolution returns the ``skill-creator`` builtin as the capturing skill for an
authoring request when it is installed and active — trigger-grade, so it
inherits the trigger channel's unconditional capture and stand-down rights.
When that builtin is disabled or absent, and for every lifecycle request, the
turn is still marked so no OTHER skill may capture it; the ``create-skill``
router tool or the skill app-commands then own the turn on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from jarvis.skills.explicit_request import _INFO_QUESTION_OPENER_RE, _SKILL_WORD_RE
from jarvis.skills.match_eval import (
    BAND_FIRE,
    BAND_NONE,
    SOURCE_NONE,
    SOURCE_TRIGGER,
    TRIGGER_MATCH_SCORE,
    MatchCandidate,
    MatchDecision,
)

#: The builtin that carries the authoring instructions (``jarvis/skills/builtin/
#: skill-creator/SKILL.md``). Resolved by name from the live registry so a user
#: who disables it opts out of the instruction card, not out of the protection.
AUTHORING_SKILL_NAME = "skill-creator"

MetaKind = Literal["authoring", "lifecycle"]

# The internal-artifact nouns a spoken authoring request uses for "a skill" in
# this product (the skill-creator builtin's own ``intent_objects`` vocabulary,
# plus their compounds and plurals): skill, routine (incl. compounds),
# automation, workflow, and their German and Spanish forms. A brand skill's
# trigger must lose to a request that creates ANY of these — "build me an
# automation that summarises Slack on Mondays" is the same failure as the live
# one, one synonym away.
_ARTIFACT_NOUN = (
    r"(?:skills?|\w*routinen?|automatisierung(?:en)?|automations?|"  # i18n-allow: speech vocab
    r"workflows?|abl[äa]uf\w*|f[äa]higkeit(?:en)?|"  # i18n-allow: speech vocab
    r"rutinas?|automatizaci[óo]n(?:es)?|flujos?)"  # i18n-allow: speech vocab
)
_ARTIFACT_NOUN_RE = re.compile(rf"\b{_ARTIFACT_NOUN}\b", re.IGNORECASE)

# Creation verbs, DE / EN / ES, as STEMS so every spoken conjugation matches
# ("erstell", "erstellst", "erstellen", "erstelle"; "create", "creates",
# "creating"; "crea", "crear", "creas"). Umlaut and digraph forms both listed.
# Deliberately EXCLUDES the run/use verbs ("nutz", "starte", "run", "use") —
# those belong to :mod:`jarvis.skills.explicit_request` — and the delete /
# disable verbs, which are the LIFECYCLE kind below.
_VERB_STEMS = (
    # German
    r"erstell\w*|erzeug\w*|kreier\w*|generier\w*|entwick\w*|entwerf\w*|"  # i18n-allow: speech vocab
    r"programmier\w*|definier\w*|konfigurier\w*|schreib\w*|"  # i18n-allow: speech vocab
    r"bau\w*|mach\w*|anleg\w*|leg\w*|einricht\w*|richt\w*|"  # i18n-allow: speech vocab
    # English
    r"creat\w*|build\w*|mak\w*|make|writ\w*|generat\w*|design\w*|set\s?up|"
    r"add\w*|defin\w*|author\w*|develop\w*|program\w*|turn\w*|"
    # Spanish
    r"cre\w*|constru\w*|haz|hac\w*|escrib\w*|gener\w*|dise[ñn]\w*|"  # i18n-allow: speech vocab
    r"a[ñn]ad\w*|agreg\w*|configur\w*"  # i18n-allow: speech vocab
)

# Shape A — an indefinite or "new" article ahead of the noun. Up to two words
# may sit between them ("einen ganz neuen skill", "a brand new skill"), but
# never a genitive / possessive / definite word: "eine Übersicht MEINER  # i18n-allow: example
# skills" and "a list OF my skills" are requests ABOUT skills, not for one.
_GAP_STOP = (
    r"meiner|meine|meinen|meines|deiner|deine|seiner|ihrer|"  # i18n-allow: speech vocab
    r"unserer|aller|der|des|dieser|jener|"  # i18n-allow: speech vocab
    r"my|your|his|her|our|their|of|the|these|those|all|"
    r"de|mis|tus|sus|los|las|del"  # i18n-allow: speech vocab
)
_INDEFINITE_NOUN_RE = re.compile(
    r"\b(?:ein(?:e|en|es)?|neue[nrs]?|a|an|another|new|"  # i18n-allow: speech vocab
    r"un|una|nuevo|nueva|otro|otra)\s+"  # i18n-allow: speech vocab
    rf"(?:(?!(?:{_GAP_STOP})\b)[\w-]+\s+){{0,2}}{_ARTIFACT_NOUN}\b",
    re.IGNORECASE,
)

# Shape B — a creation verb shortly AFTER the noun (German verb-final clauses:
# "… einen skill erstellst", "… skill zu erstellen", "skill für mich  # i18n-allow: example
# bauen"). Only the unambiguous creation stems here — "mach", "leg", "turn",
# "add" after a noun are usually toggles ("mach den skill AN"). A German
# participle inside a relative clause ("den skill, den du gestern erstellt
# HAST") and an English past tense ("the skill I createD") describe an
# EXISTING skill and are excluded, so "starte den skill den du erstellt hast"
# and "run the skill I created" stay with the run/use paths.
_NOUN_THEN_VERB_RE = re.compile(
    rf"\b{_ARTIFACT_NOUN}\s+(?:[\w-]+\s+){{0,3}}(?:"
    r"(?:erstell\w*|erzeug\w*|kreier\w*|generier\w*|anleg\w*|"  # i18n-allow: speech vocab
    r"einricht\w*|schreib\w*|bau\w*|entwick\w*|entwerf\w*|"  # i18n-allow: speech vocab
    r"programmier\w*)"  # i18n-allow: speech vocab
    r"(?!\s+(?:hast|hattest|hattet|habe|hatte|haben|hat|wurde|worden)\b)"  # i18n-allow: vocab
    r"|"
    r"creat(?:e|es|ing)|generat(?:e|es|ing)|build(?:s|ing)?|writ(?:e|es|ing)|"
    r"design(?:s|ing)?|"
    # Spanish present / infinitive forms only — ``crea\w*`` would swallow the
    # English "created" and ``gener\w*`` the English "generated".
    r"crea(?:r|s|mos|n|ndo)?|constru(?:ir|ye|yes|yamos|yen|yendo|ya)?|"  # i18n-allow: speech vocab
    r"gener(?:a|ar|as|amos|an|ando|e|es)?|escrib\w*|dise[ñn]\w*"  # i18n-allow: speech vocab
    r")\b",
    re.IGNORECASE,
)

# Shape C — a creation verb right BEFORE the noun, with only politeness /
# indefinite glue between ("erstell mir mal skill morgenroutine", "create skill
# …", "haz un skill"). Definite articles are NOT glue: "mach DEN skill aus".  # i18n-allow: example
_VERB_THEN_NOUN_RE = re.compile(
    rf"\b(?:{_VERB_STEMS})\s+"
    r"(?:(?:mir|me|uns|us|bitte|please|doch|mal|jetzt|now|schnell|"  # i18n-allow: speech vocab
    r"quick(?:ly)?|einen?|neuen?|new|a|an|un|una|nuevo|nueva|otro|otra)"  # i18n-allow: speech vocab
    r"\s+){0,4}"
    rf"{_ARTIFACT_NOUN}\b",
    re.IGNORECASE,
)

_ANY_VERB_RE = re.compile(rf"\b(?:{_VERB_STEMS})\b", re.IGNORECASE)

# Lifecycle verbs, DE / EN / ES, as stems. "aktivier" is here too: on an ACTIVE
# named skill the explicit-request resolver already claimed it as a use-verb
# and runs first; what reaches this resolver is "aktiviere den Skill X" for a
# DRAFT or disabled skill — an enable, not a run. Deliberately excludes the
# creation verbs above and the run/use verbs.
_LIFECYCLE_VERB_RE = re.compile(
    r"\b(?:"
    r"deaktivier\w*|aktivier\w*|abschalt\w*|ausschalt\w*|einschalt\w*|"  # i18n-allow: speech vocab
    r"l[öo]sch\w*|loesch\w*|entfern\w*|zeig\w*|list\w*|auflist\w*|"  # i18n-allow: speech vocab
    r"schalt\w*|"  # i18n-allow: speech vocab
    r"disable\w*|enable\w*|delete\w*|remove\w*|show\w*|turn\s+(?:on|off)|"
    r"switch\s+(?:on|off)|"
    r"desactiv\w*|activ\w*|elimin\w*|borr\w*|muestr\w*|mostrar|quit\w*"  # i18n-allow: speech vocab
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AuthoringResolution:
    """What a request ABOUT skills resolved to.

    ``kind`` is ``"authoring"`` (a new skill) or ``"lifecycle"`` (switch off /
    on, delete, list, show). ``skill`` is the active ``skill-creator`` builtin
    for an authoring request when installed, else ``None`` — the turn is a
    meta request either way (that is what ``decision`` records); the
    difference is only whether an instruction card captures it or the router
    tools stand alone.
    """

    skill: Any | None
    decision: MatchDecision
    kind: MetaKind = "authoring"


def is_skill_authoring_request(utterance: str) -> bool:
    """True when the user asks for a NEW skill (or routine / automation …).

    Pure regex, no registry: usable by the force-spawn guard and the evidence
    gate before any skill lookup. Never raises.
    """
    if not utterance:
        return False
    try:
        if not _ARTIFACT_NOUN_RE.search(utterance):
            return False
        if not _ANY_VERB_RE.search(utterance):
            return False
        if _INFO_QUESTION_OPENER_RE.match(utterance):
            # A question ABOUT creating skills must be ANSWERED, never acted on.
            return False
        return bool(
            _INDEFINITE_NOUN_RE.search(utterance)
            or _NOUN_THEN_VERB_RE.search(utterance)
            or _VERB_THEN_NOUN_RE.search(utterance)
        )
    except Exception:  # noqa: BLE001 — detection must never break a turn
        return False


def is_skill_lifecycle_request(utterance: str) -> bool:
    """True when the user asks to switch off / on, delete, list or show a SKILL.

    The literal word "skill(s)" is required — "switch the light off" names no
    skill and stays with the smart-home connector. Never raises.
    """
    if not utterance:
        return False
    try:
        if not _SKILL_WORD_RE.search(utterance):
            return False
        if not _LIFECYCLE_VERB_RE.search(utterance):
            return False
        if _INFO_QUESTION_OPENER_RE.match(utterance):
            return False
        return True
    except Exception:  # noqa: BLE001 — detection must never break a turn
        return False


def skill_meta_kind(utterance: str) -> MetaKind | None:
    """``"authoring"``, ``"lifecycle"`` or ``None`` — creation outranks lifecycle
    ("create a new skill and delete the old one" is authoring first)."""
    if is_skill_authoring_request(utterance):
        return "authoring"
    if is_skill_lifecycle_request(utterance):
        return "lifecycle"
    return None


def resolve_skill_authoring_request(utterance: str, registry: Any) -> AuthoringResolution | None:
    """Resolve a request ABOUT skills to the authoring builtin, or to "nobody".

    Returns ``None`` when the utterance is neither an authoring nor a lifecycle
    request. Otherwise returns an :class:`AuthoringResolution` whose
    ``decision`` carries ``SOURCE_TRIGGER`` / ``BAND_FIRE`` when an authoring
    request meets an active ``skill-creator`` (a stated intent is trigger-grade
    evidence, exactly like a spoken skill name), and an empty ``BAND_NONE``
    decision otherwise — the caller then lets NO skill capture. Never raises.
    """
    kind = skill_meta_kind(utterance)
    if kind is None:
        return None
    nobody = AuthoringResolution(
        skill=None,
        decision=MatchDecision(band=BAND_NONE, source=SOURCE_NONE),
        kind=kind,
    )
    if kind == "lifecycle":
        return nobody
    try:
        skill = None
        if registry is not None:
            for candidate in registry.list_active():
                if str(getattr(candidate, "name", "") or "") == AUTHORING_SKILL_NAME:
                    skill = candidate
                    break
        if skill is None:
            return nobody
        candidate = MatchCandidate(
            skill_name=AUTHORING_SKILL_NAME,
            score=TRIGGER_MATCH_SCORE,
            band=BAND_FIRE,
            source=SOURCE_TRIGGER,
            evidence="skill",
            reason="user asked to create a new skill (authoring request)",
        )
        decision = MatchDecision(
            band=BAND_FIRE,
            source=SOURCE_TRIGGER,
            top=candidate,
            candidates=(candidate,),
        )
        return AuthoringResolution(skill=skill, decision=decision, kind="authoring")
    except Exception:  # noqa: BLE001 — resolution must never break a turn
        return nobody


__all__ = [
    "AUTHORING_SKILL_NAME",
    "AuthoringResolution",
    "MetaKind",
    "is_skill_authoring_request",
    "is_skill_lifecycle_request",
    "resolve_skill_authoring_request",
    "skill_meta_kind",
]
