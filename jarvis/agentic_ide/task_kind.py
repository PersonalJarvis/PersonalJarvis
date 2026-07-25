"""Which kind of work is this instruction asking for?

The Claude 5 prompting documentation is explicit that the *same* prompt
structure is wrong for different kinds of work, and that some instructions are
actively harmful in the wrong place. Two examples that drove this module:

* On a code review, "only report high-severity issues" or "be conservative" is
  followed literally and suppresses real findings — a review prompt must ask
  for everything and filter in a later pass.
* On an investigation, the deliverable is the diagnosis. Authorising a fix in
  the same breath makes the agent change code the user has not seen a finding
  for yet.

So the composer injects one rule set, not all of them: four short, coherent
sets beat one long self-contradicting one. This module picks which.

Matching is deliberately conservative and cheap: regex over verb stems, no
model call, no IO. A misclassification is inexpensive by construction — every
rule set is a mild steer and none of them forbids the agent from doing the
work — so an unrecognised instruction falls to a neutral set rather than to a
guess.

The German and Spanish stems here are speech-recognition *input vocabulary*:
the words a person actually says when handing work to an agent. They are
matching data, not prose (see CLAUDE.md §1, closed list item 3), and each such
line carries an inline marker.
"""
from __future__ import annotations

import re

KIND_IMPLEMENT = "implement"
KIND_REVIEW = "review"
KIND_INVESTIGATE = "investigate"
KIND_QUESTION = "question"
KIND_NEUTRAL = "neutral"

_INVESTIGATE_EN = (
    r"why\s+(?:does|do|is|are|did|was|were|can'?t|won'?t)"
    r"|find\s+out\s+why|figure\s+out\s+why|root[-\s]?cause"
    r"|investigate|diagnose|debug|troubleshoot|trace\s+down"
)
_INVESTIGATE_DE = (
    r"|warum\s+(?:funktioniert|geht|l[aä]uft|ist|sind"  # i18n-allow: input vocab
    r"|schl[aä]gt|f[aä]llt|passiert|kommt|bricht"  # i18n-allow: input vocab
    r"|st[uü]rzt|das|der|die|es|nicht)"  # i18n-allow: input vocab
    r"|finde?\s+heraus|untersuch\w*"  # i18n-allow: input vocab
    r"|diagnostizier\w*|debugg?e?\w*"  # i18n-allow: input vocab
)
_INVESTIGATE_ES = (
    r"|por\s+qu[eé]\s+(?:falla|no|se|est[aá])"  # i18n-allow: input vocab
    r"|averigua\w*|investiga\w*|depura\w*"  # i18n-allow: input vocab
)

_REVIEW_EN = (
    r"(?:code[-\s]?)?review|audit|critique|assess|go\s+over"
    r"|look\s+over|check\s+over|sanity[-\s]?check"
)
_REVIEW_DE = (
    r"|review\w*|[uü]berpr[uü]f\w*|pr[uü]f\w*"  # i18n-allow: input vocab
    r"|begutacht\w*|durchsieh\w*|kontrollier\w*"  # i18n-allow: input vocab
)
_REVIEW_ES = r"|revisa\w*|audita\w*|eval[uú]a\w*"  # i18n-allow: input vocab

_IMPLEMENT_EN = (
    r"add|build|implement|write|create|make|refactor|rename|move"
    r"|fix|repair|patch|migrate|port|wire\s+up|hook\s+up|extend"
    r"|remove|delete|drop|replace|update|upgrade|bump"
)
_IMPLEMENT_DE = (
    r"|bau\w*|erstell\w*|implementier\w*|schreib\w*"  # i18n-allow: input vocab
    r"|f[uü]ge?\b|erg[aä]nz\w*|repari\w*|behe\w*"  # i18n-allow: input vocab
    r"|fixe?\w*|refactor\w*|benenn\w*|verschieb\w*"  # i18n-allow: input vocab
    r"|entfern\w*|l[oö]sch\w*|ersetz\w*|aktualisier\w*"  # i18n-allow: input vocab
)
_IMPLEMENT_ES = (
    r"|a[ñn]ade?\w*|agrega\w*|implementa\w*|escribe?\w*"  # i18n-allow: input vocab
    r"|crea\w*|arregla\w*|corrige?\w*|refactoriza\w*"  # i18n-allow: input vocab
    r"|elimina\w*|reemplaza\w*|actualiza\w*"  # i18n-allow: input vocab
)

_QUESTION_EN = (
    r"how\s+(?:does|do|is|are|can|would|should)|what\s+(?:does|do|is|are)"
    r"|where\s+(?:does|do|is|are)|which\s+\w+\s+(?:does|do|is|are)"
    r"|explain|describe|walk\s+me\s+through|tell\s+me\s+(?:about|how)"
)
_QUESTION_DE = (
    r"|wie\s+(?:funktioniert|arbeitet|l[aä]uft"  # i18n-allow: input vocab
    r"|ist|sind|macht|h[aä]ngt)"  # i18n-allow: input vocab
    r"|was\s+(?:macht|tut|ist|sind|bedeutet|passiert)"  # i18n-allow: input vocab
    r"|wo\s+(?:ist|sind|liegt|steht|wird)"  # i18n-allow: input vocab
    r"|erkl[aä]r\w*|beschreib\w*"  # i18n-allow: input vocab
)
_QUESTION_ES = (
    r"|c[oó]mo\s+(?:funciona|trabaja|es)"  # i18n-allow: input vocab
    r"|qu[eé]\s+(?:hace|es|son|significa)"  # i18n-allow: input vocab
    r"|d[oó]nde\s+(?:est[aá]|se)|explica\w*|describe?\w*"  # i18n-allow: input vocab
)


def _compile(*parts: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "".join(parts) + r")\b", re.IGNORECASE)


# The winner is the kind whose pattern matches EARLIEST in the sentence, so the
# leading verb decides: "build a review dashboard" is an implementation request
# even though "review" also matches, because "build" comes first. Where two
# kinds match at the same offset, the tuple order below breaks the tie —
# investigation first, because "why does X fail" is an investigation even
# though "fail" reads like a defect report.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (KIND_INVESTIGATE, _compile(_INVESTIGATE_EN, _INVESTIGATE_DE, _INVESTIGATE_ES)),
    (KIND_REVIEW, _compile(_REVIEW_EN, _REVIEW_DE, _REVIEW_ES)),
    (KIND_IMPLEMENT, _compile(_IMPLEMENT_EN, _IMPLEMENT_DE, _IMPLEMENT_ES)),
    (KIND_QUESTION, _compile(_QUESTION_EN, _QUESTION_DE, _QUESTION_ES)),
)


def classify(instruction: str) -> str:
    """The kind of work ``instruction`` asks for, or ``KIND_NEUTRAL``.

    Never raises and never returns an unknown value: callers may index a rule
    table with the result directly.
    """
    text = " ".join((instruction or "").split())
    if not text:
        return KIND_NEUTRAL

    best_kind = KIND_NEUTRAL
    best_at = len(text) + 1
    for kind, pattern in _PATTERNS:
        match = pattern.search(text)
        if match is not None and match.start() < best_at:
            best_kind, best_at = kind, match.start()
    return best_kind


__all__ = [
    "KIND_IMPLEMENT",
    "KIND_INVESTIGATE",
    "KIND_NEUTRAL",
    "KIND_QUESTION",
    "KIND_REVIEW",
    "classify",
]
