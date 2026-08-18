"""Intent-level router: selects the provider/model level based on prompt analysis.

Orthogonal to the tier router from Phase 5 (`jarvis/brain/router.py`):
- **Tier router** decides *what to do* (trivial / direct_action / spawn_worker).
- **Intent-level router** (here) decides *which provider level* within an already
  chosen tier (fast / deep / code). Used by `BrainManager` once the tier selection
  is fixed and a brain is being constructed from the fallback chain.

Goal: no extra LLM-call latency for routing. Heuristic-based — but the heuristic
decides only when it can be RIGHT, and the capable model has the last word.

**The capable model is the default.** A keyword may promote a turn to CODE or
confirm DEEP, but no keyword may demote a substantive request to the cheap fast
tier. That is exactly what used to happen: a ~40-pattern substring list ran over
the whole utterance, so "Ok, kannst du mir bei meiner Steuererklärung helfen?"  # i18n-allow
matched ``\\bok\\b``, silently took Haiku, and the user got a worse answer than
the product can produce — recorded in a debug log and nowhere else.

Two things, and only two, still reach FAST:

- **A closed-set one-shot** — a greeting, a thank-you, an acknowledgement, or a
  clock/date question. Matched against the WHOLE utterance, so a greeting that
  merely OPENS a real question no longer counts.
- **A short imperative command** — an action verb at the START, at most
  ``_MAX_ONE_SHOT_TOKENS`` words in total, no subclause, no question. "öffne  # i18n-allow
  notepad", "klick auf submit". Anything longer carries reasoning the fast tier
  cannot do, so it goes to the capable model.

Everything else — including every unrecognised utterance — is DEEP. A wrong
DEEP costs latency and tokens; a wrong FAST costs the answer.

- **DEEP** (Opus): reasoning, analysis, planning, explaining, comparing, and
  the default for anything not proven trivial.
- **CODE** (Jarvis-Agent Heavy-Worker): explicit coding tasks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IntentLevel = Literal["fast", "deep", "code"]

#: A one-shot imperative is short. Beyond this many words an utterance carries
#: qualifiers, context or a second thought — work for the capable model.
_MAX_ONE_SHOT_TOKENS = 6

_TOKEN_RE = re.compile(r"[\w'’-]+", re.UNICODE)

# Utterances that are complete in themselves and need no reasoning whatsoever.
# Matched against the WHOLE text (anchored), never as a substring: "danke" is a
# one-shot, "Danke, und wie geht das jetzt weiter?" is a real question.
_ONE_SHOT_PATTERNS = [
    # Greetings — DE / EN / ES
    r"hallo|hi|hey|moin|servus|na\b",  # i18n-allow: DE greeting
    r"gu?ten\s+(?:morgen|tag|abend)",  # i18n-allow: DE greeting
    r"hello|hey\s+there|good\s+(?:morning|afternoon|evening)",
    r"hola|buenos\s+d[íi]as|buenas\s+(?:tardes|noches)",
    # Thanks
    r"danke(?:\s+(?:sch[öo]n|dir|sehr|vielmals))?|vielen\s+dank",  # i18n-allow: DE thanks
    r"thanks(?:\s+a\s+lot)?|thank\s+you|cheers",
    r"gracias(?:\s+mil)?|muchas\s+gracias",
    # Acknowledgements
    r"ok(?:ay)?|alles\s+klar|passt|verstanden|geht\s+klar",  # i18n-allow: DE acknowledgement
    r"alright|all\s+right|got\s+it|understood|sounds\s+good|sure",
    r"vale|entendido|de\s+acuerdo",
    # Farewells
    r"tsch[üu]ss|ciao|bis\s+(?:sp[äa]ter|dann|morgen)|gute\s+nacht",  # i18n-allow: DE farewell
    r"bye|goodbye|see\s+you|good\s+night",
    r"adi[óo]s|hasta\s+luego|buenas\s+noches",
    # Clock / date — a single lookup with one right answer.
    r"wie\s+sp[äa]e?t\s+(?:ist\s+es|haben\s+wir)",  # i18n-allow: DE clock question
    r"wie\s?viel\s+uhr\s+(?:ist\s+es|haben\s+wir)",  # i18n-allow: DE clock question
    r"welchen?\s+(?:tag|datum)\s+(?:ist|haben\s+wir)\s*(?:heute|wir)?",  # i18n-allow
    r"welches\s+datum\s+(?:ist\s+heute|haben\s+wir)",  # i18n-allow: DE date question
    r"what(?:'s|\s+is)\s+the\s+(?:time|date)|what\s+time\s+is\s+it",
    r"what\s+day\s+is\s+(?:it|today)",
    r"qu[ée]\s+hora\s+es|qu[ée]\s+d[íi]a\s+es(?:\s+hoy)?",
]
_ONE_SHOT_RE = re.compile(
    r"^(?:" + "|".join(_ONE_SHOT_PATTERNS) + r")[\s.!?,…]*$",
    re.IGNORECASE,
)

# A leading acknowledgement before the real request ("Ok, öffne Notepad").  # i18n-allow
# Stripped before the opener test so the ack does not hide the verb — and it
# cannot make a substantive turn fast, because the remainder still has to pass
# the opener AND the word cap.
_LEAD_IN_RE = re.compile(
    r"^(?:ok(?:ay)?|alles\s+klar|hey|hi|hallo|so|also|bitte|please|"  # i18n-allow: DE lead-in
    r"und|and|y)\b[\s,.:;!–-]+",
    re.IGNORECASE,
)

# Imperative action verbs, anchored at the START of the utterance. A verb in the
# MIDDLE of a sentence is part of a description, not a command — that is how
# "such mal eine Lösung für mein Speicherleck" used to reach Haiku.  # i18n-allow
_FAST_OPENER_PATTERNS = [
    # German imperatives
    r"[öo]e?ffne|mach|schlie[ßs]|beende|starte?|spawne?|f[üu]hr\s+aus",  # i18n-allow: DE imperative
    r"klicke?|tippe?|dr[üu]cke?|scrolle?|wechsle?|springe?",  # i18n-allow: DE imperative
    r"sage?|sprich|lies|zeige?|liste|nenne?",  # i18n-allow: DE imperative
    r"minimiere?|maximiere?|schlie[ßs]e",  # i18n-allow: DE imperative
    r"merk\s+dir|merke?|speichere?|kopiere?|f[üu]ge?\s+ein",  # i18n-allow: DE imperative
    r"suche?|finde|[öo]e?ffne",  # i18n-allow: DE imperative
    r"spiele?|stopp|pausiere?|stelle?\s+lauter|stelle?\s+leiser",  # i18n-allow: DE imperative
    # English imperatives
    r"open|close|quit|launch|run|start|spawn|execute",
    r"click|type|press|scroll|switch|jump",
    r"say|read|show|list|display|name",
    r"minimi[sz]e|maximi[sz]e",
    r"remember|save|copy|paste",
    r"search|find|look\s+up",
    r"play|stop|pause|mute|unmute",
    # Spanish imperatives
    r"abre|cierra|inicia|lanza|ejecuta",
    r"haz\s+clic|escribe|pulsa|cambia",
    r"di|muestra|lista|lee",
    r"recuerda|guarda|copia|pega",
    r"busca|encuentra|reproduce|para|pausa",
]
_FAST_OPENER_RE = re.compile(
    r"^(?:" + "|".join(_FAST_OPENER_PATTERNS) + r")\b",
    re.IGNORECASE,
)

# A subclause turns a command into a request with reasoning in it. Any of these
# vetoes the fast route regardless of how short the utterance is.
_SUBCLAUSE_RE = re.compile(
    r"\b(?:weil|damit|obwohl|falls|sobald|w[äa]hrend|sodass|dass|ob|"  # i18n-allow
    r"aber|oder|wenn|warum|wieso|weshalb|"  # i18n-allow
    r"because|although|whether|unless|while|so\s+that|but|or|if|why|how\s+come|"
    r"porque|aunque|mientras|pero|si|por\s+qu[ée])\b",
    re.IGNORECASE,
)

# Complex reasoning tasks (Opus). These CONFIRM deep — they no longer have to
# fight a fast keyword for it, since deep is the default.
_DEEP_PATTERNS = [
    # German — `\w*` instead of `\b` at the end to catch conjugations
    r"\brecherchier\w*", r"\banalysier\w*", r"\bplane\w*\b", r"\bplanung\b",
    r"\berkl[äa]r\w*", r"\bvergleich\w*", r"\bunterschied\w*",  # i18n-allow
    r"\bschreib\w*", r"\bformulier\w*", r"\bverfass\w*",
    r"\bbau(e|t)?\b.*\bmir\b", r"\bentwickel\w*", r"\bentwerf\w*",
    r"\b[üu]berleg\w*", r"\bdenk gr[üu]ndlich\b", r"\bnachdenk\w*",  # i18n-allow
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

# Coding tasks are routed to the Jarvis-Agent heavy worker.
_CODE_PATTERNS = [
    r"\bcode\b.*(f[üu]r|for|write|schreib)",  # i18n-allow
    r"\bimplementier\b", r"\bimplement\b",
    r"\bfix bug\b", r"\bfehler finden\b", r"\bdebug\b",
    r"\brefactor\b", r"\brefaktor\b",
    r"\breview.*pr\b", r"\breview.*code\b", r"\bcode review\b",
    r"\bunit.?test\b", r"\bintegration.?test\b",
    r"\bgit commit\b", r"\bcommit.*message\b",
    r"\bpull request\b", r"\bpr\b.*?beschreib",
]

_DEEP_RE = re.compile("|".join(_DEEP_PATTERNS), re.IGNORECASE)
_CODE_RE = re.compile("|".join(_CODE_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    level: IntentLevel
    reason: str
    matched: str = ""


def _is_short_imperative(text: str) -> bool:
    """True for a command that is provably a one-shot: verb first, and short.

    The word cap is the load-bearing part. "öffne notepad" is a one-shot;  # i18n-allow
    "such mir raus, warum der Build seit gestern rot ist" opens with the same  # i18n-allow
    kind of verb and is not.
    """
    stripped = _LEAD_IN_RE.sub("", text).strip()
    if not stripped or "?" in stripped:
        return False
    if not _FAST_OPENER_RE.match(stripped):
        return False
    if _SUBCLAUSE_RE.search(stripped):
        return False
    return len(_TOKEN_RE.findall(stripped)) <= _MAX_ONE_SHOT_TOKENS


def classify(user_text: str) -> RoutingDecision:
    """Classifies user text as fast/deep/code. No LLM call.

    Order matters: coding wins, then deep keywords, then the two proven-trivial
    fast routes, and DEEP takes everything left over.
    """
    t = user_text.strip()
    if not t:
        return RoutingDecision(level="fast", reason="empty")

    # 1. Coding pattern takes precedence (will use the harness later)
    m = _CODE_RE.search(t)
    if m:
        return RoutingDecision(level="code", reason="code-keyword", matched=m.group(0))

    # 2. An explicit reasoning verb confirms deep.
    m = _DEEP_RE.search(t)
    if m:
        return RoutingDecision(level="deep", reason="deep-keyword", matched=m.group(0))

    # 3. A closed-set one-shot: the WHOLE utterance is a greeting, a thank-you,
    #    an acknowledgement, or a clock/date question.
    m = _ONE_SHOT_RE.match(t)
    if m:
        return RoutingDecision(level="fast", reason="one-shot", matched=m.group(0))

    # 4. A short imperative command with no subclause and no question.
    if _is_short_imperative(t):
        return RoutingDecision(level="fast", reason="short-imperative")

    # 5. Everything else gets the capable model. An utterance we cannot prove
    #    trivial is not trivial — a wrong deep costs latency, a wrong fast costs
    #    the answer.
    return RoutingDecision(level="deep", reason="capable-default")
