"""Intent-level router: selects the provider/model level based on prompt analysis.

Orthogonal to the tier router from Phase 5 (`jarvis/brain/router.py`):
- **Tier router** decides *what to do* (trivial / direct_action / spawn_worker).
- **Intent-level router** (here) decides *which provider level* within an already
  chosen tier (fast / deep / code). Used by `BrainManager` once the tier selection
  is fixed and a brain is being constructed from the fallback chain.

Goal: no extra LLM-call latency for routing. Heuristic-based:

- **FAST** (Haiku): simple actions, tool calls, smalltalk responses
  Keywords: öffne, spawn, starte, klicke, tippe, sag, merk, zeig, such, etc.  # i18n-allow
  Heuristic: short (<80 chars), imperative, one clear verb + object

- **DEEP** (Opus): reasoning, analysis, planning, explaining, comparing, coding
  Keywords: recherchiere, analysiere, plane, erkläre, vergleich, schreib, baue,  # i18n-allow
  debug, refactor, überlege, zusammenfass, etc.  # i18n-allow
  Heuristic: long, many subclauses, multiple aspects, question word + depth

- **CODE** (OpenClaw Heavy-Worker): explicit coding tasks
  Keywords: code, implementier, fix bug, review pr, git commit, test

On ambiguity → DEEP (the user must not think Jarvis is dumb).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IntentLevel = Literal["fast", "deep", "code"]


# Simple actions / tool triggers (Haiku is sufficient).
_FAST_PATTERNS = [
    # German — imperatives
    r"\böffne\b", r"\bstart(e|et)?\b", r"\bstarte\b", r"\bspawn(e)?\b",  # i18n-allow
    r"\bklick(e|t)?\b", r"\btipp(e|t)?\b", r"\btype\b",
    r"\bsag\b", r"\bsage\b", r"\bsprich\b",
    r"\bzeig(e|t)?\b", r"\bliste\b", r"\bdisplay\b",
    r"\bmach auf\b", r"\bmach zu\b", r"\bminimier\b", r"\bmaximier\b",
    r"\bmerk dir\b", r"\bspeicher\b",
    r"\bkopiere\b", r"\bfüg(e)? ein\b",  # i18n-allow
    r"\bsuch\b", r"\bfinde\b",
    r"\bwie spät\b", r"\bdatum\b",  # i18n-allow
    r"\bwelcher tag\b",
    # Englisch
    r"\bopen\b", r"\bclose\b", r"\blaunch\b", r"\brun\b",
    r"\bclick\b", r"\btype\b", r"\bshow\b", r"\blist\b",
    r"\bremember\b", r"\bsave\b", r"\bcopy\b",
    r"\bwhat time\b", r"\bwhat date\b",
    # Hangup / Smalltalk
    r"\bhallo\b", r"\bhi\b", r"\bguten morgen\b",
    r"\bdanke\b", r"\bthank you\b", r"\bokay\b", r"\bok\b",
]

# Complex reasoning tasks (Opus).
_DEEP_PATTERNS = [
    # German — `\w*` instead of `\b` at the end to catch conjugations
    r"\brecherchier\w*", r"\banalysier\w*", r"\bplane\w*\b", r"\bplanung\b",
    r"\berklär\w*", r"\bvergleich\w*", r"\bunterschied\w*",  # i18n-allow
    r"\bschreib\w*", r"\bformulier\w*", r"\bverfass\w*",
    r"\bbau(e|t)?\b.*\bmir\b", r"\bentwickel\w*", r"\bentwerf\w*",
    r"\büberleg\w*", r"\bdenk gründlich\b", r"\bnachdenk\w*",  # i18n-allow
    r"\bzusammenfass\w*", r"\bfass\w*.*zusammen",
    r"\bwarum\b", r"\bwieso\b", r"\bweshalb\b",
    r"\boptimier\w*", r"\bverbesser\w*",
    r"\banleitung\b", r"\btutorial\b", r"\bkonzept\b", r"\barchitektur\b",
    # English
    r"\bresearch\w*", r"\banalyz\w*", r"\banalyse\w*",
    r"\bdesign\w*", r"\bexplain\w*", r"\bcompare\w*", r"\bdifferenc\w*",
    r"\bwrite\b.*\bfor me\b", r"\bdraft\w*",
    r"\bthink hard\b", r"\bdeep think\b", r"\bdeeply\b",
    r"\bsummariz\w*", r"\boptimiz\w*", r"\bimprove\w*",
    r"\bplan\b.*\?", r"\bwhy\b.*\?",
]

# Coding tasks are routed to the OpenClaw heavy worker.
_CODE_PATTERNS = [
    r"\bcode\b.*(für|for|write|schreib)",  # i18n-allow
    r"\bimplementier\b", r"\bimplement\b",
    r"\bfix bug\b", r"\bfehler finden\b", r"\bdebug\b",
    r"\brefactor\b", r"\brefaktor\b",
    r"\breview.*pr\b", r"\breview.*code\b", r"\bcode review\b",
    r"\bunit.?test\b", r"\bintegration.?test\b",
    r"\bgit commit\b", r"\bcommit.*message\b",
    r"\bpull request\b", r"\bpr\b.*?beschreib",
]

_FAST_RE = re.compile("|".join(_FAST_PATTERNS), re.IGNORECASE)
_DEEP_RE = re.compile("|".join(_DEEP_PATTERNS), re.IGNORECASE)
_CODE_RE = re.compile("|".join(_CODE_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    level: IntentLevel
    reason: str
    matched: str = ""


def classify(user_text: str) -> RoutingDecision:
    """Classifies user text as fast/deep/code. No LLM call."""
    t = user_text.strip()
    if not t:
        return RoutingDecision(level="fast", reason="leer")

    # 1. Coding pattern takes precedence (will use the harness later)
    m = _CODE_RE.search(t)
    if m:
        return RoutingDecision(level="code", reason="code-keyword", matched=m.group(0))

    # 2. Deep pattern beats fast pattern (user wants depth when detectable)
    m = _DEEP_RE.search(t)
    if m:
        return RoutingDecision(level="deep", reason="deep-keyword", matched=m.group(0))

    # 3. Fast-Pattern
    m = _FAST_RE.search(t)
    if m:
        return RoutingDecision(level="fast", reason="fast-keyword", matched=m.group(0))

    # 4. Length heuristic as tiebreaker
    # Short statements → a simple answer is probably sufficient
    if len(t) < 30:
        return RoutingDecision(level="fast", reason="short-default")

    # Long / complex queries → Opus to ensure quality
    if len(t) > 120 or t.count(",") >= 2 or t.count("?") >= 1:
        return RoutingDecision(level="deep", reason="long-or-question")

    # Default: mid-range fast — when in doubt use Haiku (speed > depth)
    return RoutingDecision(level="fast", reason="default")
