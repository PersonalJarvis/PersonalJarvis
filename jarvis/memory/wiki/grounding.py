"""Deterministic grounding checks for model-proposed Wiki facts.

These checks are deliberately narrow.  They do not try to prove arbitrary
natural-language entailment; they only block a known high-cost failure mode:
turning a topic question into a personal interest or preference.  The LLM
still handles general semantic judgement in Stage 2.

Besides the block/pass verdict, :func:`classify_user_attitude_evidence`
grades HOW an attitude or habit claim about the user is grounded: an
``explicit`` first-person assertion ("I love golf"), or a ``behavioral``
first-person lived-experience report ("I was out on the golf course again
on Saturday with my buddies") that supports the claim without literally
naming a preference.  Topic questions and bare topic mentions ground
neither and stay blocked.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

_EVIDENCE_PREFIX_RE = re.compile(
    r"\AEvidence user turn \[[^\]\r\n]{1,80}\]:\s*",
    re.IGNORECASE,
)
_PRIOR_CONTEXT_RE = re.compile(
    r"\r?\nPrior user context \[[^\]\r\n]{1,80}\]:",
    re.IGNORECASE,
)
_CLAUSE_RE = re.compile(r"([^.!?;,\n:\u2013\u2014]+)([.!?;,\n:\u2013\u2014]|\Z)")

# Candidate-output vocabulary.  English is the normal structured-output
# language; German and Spanish forms keep the guard equal across supported
# spoken languages when a provider mirrors the transcript language.
_CANDIDATE_INTEREST_RE = re.compile(
    r"\b(?:"
    r"interest(?:ed|s)?|curious(?:ity)?|keen\s+on|"
    r"likes?|loves?|enjoys?|prefers?|preferences?|fan\s+of|"
    r"enthusias\w*|fascinat\w*|dislikes?|hates?|avoids?|"
    r"interess(?:e|iert\w*)|mag|liebt|"  # i18n-allow: input vocabulary
    r"bevorzugt|begeistert|fasziniert|"  # i18n-allow: input vocabulary
    r"interesad[oa]s?|interes|le\s+gusta|prefiere|aficionad[oa]s?|fascina"
    r")\b",
    re.IGNORECASE,
)
_INTEREST_RATE_RE = re.compile(r"\binterest\s+rates?\b", re.IGNORECASE)
_GENERIC_USER_REF_RE = re.compile(
    r"\b(?:the\s+user|user|speaker|"
    r"der\s+benutzer|die\s+benutzerin|"  # i18n-allow: input vocabulary
    r"sprecher|sprecherin|"  # i18n-allow: input vocabulary
    r"el\s+usuario|la\s+usuaria|hablante)\b",
    re.IGNORECASE,
)
_SUBJECTLESS_INTEREST_RE = re.compile(
    r"^(?:interest(?:ed)?|curious|preference|likes?|loves?|enjoys?|prefers?|"
    r"plays?|trains?|practi[cs]es?)\b",
    re.IGNORECASE,
)

_QUESTION_PREFIX_RE = re.compile(
    r"^(?:"
    r"what|who|how|where|when|why|which|do|does|did|am|are|is|"
    r"was|were|can|could|would|should|will|have|has|"
    r"tell\s+me|explain|show\s+me|"
    r"was|wer|wie|wo|wann|warum|welch\w*|"  # i18n-allow: input vocabulary
    r"bin|bist|ist|sind|kann|koennte|wuerde|soll|"  # i18n-allow: input vocabulary
    r"erzaehl\w*|sag\s+mir|erklaer\w*|"  # i18n-allow: input vocabulary
    r"que|quien|como|donde|cuando|cual|puedo|puedes|podria|"
    r"deberia|dime|cuentame|explica"
    r")\b",
    re.IGNORECASE,
)

_POSITIVE_INTEREST_ASSERTION_RE = re.compile(
    r"\b(?:"
    r"i\s+(?:am|'m|was|remain)\s+(?:interested|curious|keen|a\s+fan)|"
    r"i\s+(?:like|love|enjoy|prefer|follow|care\s+about)|"
    r"i(?:'m|\s+am)\s+into|my\s+(?:interest|interests|preference|"
    r"preferences|favorite|favourite|hobby|hobbies)|"
    r"(?:interests|fascinates)\s+me|is\s+my\s+thing|"
    r"ich\s+interessiere\s+mich|"  # i18n-allow: input vocabulary
    r"mich\s+interessiert|"  # i18n-allow: input vocabulary
    r"ich\s+(?:mag|liebe|bevorzuge|verfolge)|"  # i18n-allow: input vocabulary
    r"mein\w*\s+(?:interesse|hobby|liebling\w*)|"  # i18n-allow: input vocabulary
    r"fasziniert\s+mich|"  # i18n-allow: input vocabulary
    r"me\s+(?:interesa|gusta|encanta|fascina)|prefiero|sigo|"
    r"mi\s+(?:interes|aficion|favorit\w*)"
    r")\b",
    re.IGNORECASE,
)
_NEGATIVE_INTEREST_ASSERTION_RE = re.compile(
    r"\b(?:"
    r"i\s+(?:am|'m|was)\s+(?:not|never|no\s+longer)\s+"
    r"(?:interested|curious|keen|a\s+fan)|"
    r"i\s+(?:do\s+not|don't|never|no\s+longer)\s+"
    r"(?:like|love|enjoy|prefer|follow|care\s+about)|"
    r"i\s+(?:dislike|hate|avoid)|not\s+my\s+thing|"
    r"ich\s+interessiere\s+mich\s+nicht|"  # i18n-allow: input vocabulary
    r"mich\s+interessiert\b[^.!?;,]{0,80}\bnicht|"  # i18n-allow: input vocab
    r"ich\s+mag\b[^.!?;,]{0,80}\bnicht|"  # i18n-allow: input vocabulary
    r"ich\s+(?:hasse|meide)|"  # i18n-allow: input vocabulary
    r"no\s+me\s+(?:interesa|gusta|encanta|fascina)|"
    r"no\s+prefiero|odio|evito"
    r")\b",
    re.IGNORECASE,
)
_NEGATIVE_CANDIDATE_RE = re.compile(
    r"\b(?:"
    r"not|never|no\s+longer"
    r")\s+(?:interested|curious|keen|a\s+fan|likes?|loves?|enjoys?|"
    r"prefers?|follows?)\b|\b(?:dislikes?|hates?|avoids?)\b",
    re.IGNORECASE,
)

# Habit/activity claims about the user ("plays golf regularly", "trains every
# week") are attitude-adjacent: they assert a durable relationship to a topic
# and therefore need the same evidence grounding as interest claims.
_CANDIDATE_HABIT_RE = re.compile(
    r"\b(?:"
    r"plays?|practi[cs]es?|trains?|"
    r"regularly|routinely|habitually|frequently|actively|"
    r"spielt|trainiert|regelmaessig|regelmassig|"  # i18n-allow: input vocabulary
    r"juega|entrena|practica|regularmente"
    r")\b",
    re.IGNORECASE,
)

# First-person lived-experience signal in a declarative evidence clause:
# the user reports DOING, habitually doing, or enjoying being at/around
# something. Matched against folded (casefolded, accent-stripped) text.
_FIRST_PERSON_EXPERIENCE_RE = re.compile(
    r"(?:"
    # en: "I play/go/train...", "we meet...", "I was/am out/at/on...",
    # "I always/usually...", "every Saturday ... I ..."
    r"\bi\s+(?:play|go|ride|drive|run|train|practi[cs]e|swim|hike|climb|"
    r"cook|bake|paint|garden|collect|volunteer|spend|meet|attend|visit)\b|"
    r"\bwe\s+(?:play|go|ride|train|meet)\b|"
    r"\bi\s+(?:was|am|'m)\s+(?:out|at|on)\b|"
    r"\bi\s+(?:always|usually|often|normally|typically)\b|"
    r"\bevery\s+(?:day|morning|evening|night|week|weekend|month|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
    # de folded: "ich spiele/gehe/fahre...", "wir spielen...", "ich war/bin",
    # "ich ... gern(e)", "jeden Samstag", "ich ... immer/oft"
    r"\bich\s+(?:spiele|gehe|fahre|laufe|trainiere|treffe|besuche|"  # i18n-allow: input vocabulary
    r"verbringe|koche|sammle|male|wandere|schwimme|klettere|war|bin)\b|"  # i18n-allow: input vocab
    r"\bwir\s+(?:spielen|gehen|fahren|treffen|trainieren)\b|"  # i18n-allow: input vocabulary
    r"\bich\s+[^.!?;,\n]{0,60}\bgerne?\b|"  # i18n-allow: input vocabulary
    r"\bich\s+[^.!?;,\n]{0,60}\b(?:immer|meistens|oft|staendig)\b|"  # i18n-allow: input vocabulary
    r"\bjede[nrs]?\s+\w+|"  # i18n-allow: input vocabulary
    # es folded: "juego/voy/entreno...", "cada sabado", "siempre/suelo"
    r"\b(?:yo\s+)?(?:juego|voy|entreno|corro|nado|cocino|paso|quedo|"
    r"asisto|visito|estuve|estaba)\b|"
    r"\bcada\s+\w+|\bsiempre\b|\bsuelo\b"
    r")",
    re.IGNORECASE,
)


def _fold(text: object) -> str:
    """Case-fold and remove accents without discarding non-Latin text."""
    raw = str(text or "").casefold().replace("\u00df", "ss")
    decomposed = unicodedata.normalize("NFKD", raw)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def focus_user_evidence(evidence_excerpt: str) -> str:
    """Return only the cited focus turn, excluding reference-only context."""
    text = str(evidence_excerpt or "").strip()
    prefix = _EVIDENCE_PREFIX_RE.match(text)
    if prefix is not None:
        text = text[prefix.end():]
    return _PRIOR_CONTEXT_RE.split(text, maxsplit=1)[0].strip()


def _asserted_interest_polarities(focus_text: str) -> set[bool]:
    """Return declaratively asserted interest polarities in the focus turn."""
    polarities: set[bool] = set()
    folded = _fold(focus_text)
    for match in _CLAUSE_RE.finditer(folded):
        clause = match.group(1).strip(" \t\u00bf\u00a1")
        terminal = match.group(2)
        if not clause or terminal == "?" or _QUESTION_PREFIX_RE.match(clause):
            continue
        if _NEGATIVE_INTEREST_ASSERTION_RE.search(clause):
            polarities.add(False)
            continue
        if _POSITIVE_INTEREST_ASSERTION_RE.search(clause):
            polarities.add(True)
    return polarities


def _behavioral_experience_grounded(focus_text: str) -> bool:
    """True when a declarative focus clause reports first-person experience."""
    folded = _fold(focus_text)
    for match in _CLAUSE_RE.finditer(folded):
        clause = match.group(1).strip(" \t¿¡")
        terminal = match.group(2)
        if not clause or terminal == "?" or _QUESTION_PREFIX_RE.match(clause):
            continue
        if _FIRST_PERSON_EXPERIENCE_RE.search(clause):
            return True
    return False


def classify_user_attitude_evidence(
    *,
    fact: str,
    subjects: Sequence[str],
    evidence_excerpt: str,
    user_slug: str,
) -> str | None:
    """Grade how an attitude/habit claim about the user is grounded.

    Returns ``"explicit"`` (literal first-person assertion, or the guard is
    not applicable to this fact family), ``"behavioral"`` (a declarative
    first-person lived-experience report supports a positive claim), or
    ``None`` (blocked: the evidence grounds neither — topic choice alone is
    not evidence of personal interest).  Ownership, plans, events, and other
    fact families return ``"explicit"`` untouched.
    """
    folded_fact = _fold(fact)
    candidate_probe = _INTEREST_RATE_RE.sub("", folded_fact)
    is_attitude = bool(_CANDIDATE_INTEREST_RE.search(candidate_probe))
    is_habit = bool(_CANDIDATE_HABIT_RE.search(folded_fact))
    if not is_attitude and not is_habit:
        return "explicit"

    folded_slug = _fold(user_slug).strip()
    folded_subjects = {_fold(subject).strip() for subject in subjects}
    user_referenced = bool(_GENERIC_USER_REF_RE.search(folded_fact))
    user_referenced = user_referenced or bool(
        _asserted_interest_polarities(folded_fact)
    )
    if folded_slug:
        user_referenced = user_referenced or bool(
            re.search(rf"(?<!\w){re.escape(folded_slug)}(?!\w)", folded_fact)
        )
        # Subject metadata is only a fallback for subjectless shorthand.  It
        # must not turn "My friend Lena likes Monaco" into a user-interest
        # claim merely because the relationship also mentioned the user.
        user_referenced = user_referenced or (
            folded_slug in folded_subjects
            and bool(_SUBJECTLESS_INTEREST_RE.match(folded_fact))
        )
    if not user_referenced:
        return "explicit"

    expected_polarity = not bool(_NEGATIVE_CANDIDATE_RE.search(folded_fact))
    focus = focus_user_evidence(evidence_excerpt)
    if expected_polarity in _asserted_interest_polarities(focus):
        return "explicit"
    if expected_polarity and _behavioral_experience_grounded(focus):
        return "behavioral"
    return None


def is_unsupported_user_interest_claim(
    *,
    fact: str,
    subjects: Sequence[str],
    evidence_excerpt: str,
    user_slug: str,
) -> bool:
    """Return whether a user attitude/habit claim lacks ANY grounding.

    Thin wrapper over :func:`classify_user_attitude_evidence`: blocked means
    the evidence contains neither an explicit assertion nor a first-person
    lived-experience report supporting the claim.
    """
    return (
        classify_user_attitude_evidence(
            fact=fact,
            subjects=subjects,
            evidence_excerpt=evidence_excerpt,
            user_slug=user_slug,
        )
        is None
    )


__all__ = [
    "classify_user_attitude_evidence",
    "focus_user_evidence",
    "is_unsupported_user_interest_claim",
]
