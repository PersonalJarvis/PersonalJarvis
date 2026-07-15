"""Deterministic grounding checks for model-proposed Wiki facts.

These checks are deliberately narrow.  They do not try to prove arbitrary
natural-language entailment; they only block a known high-cost failure mode:
turning a topic question into a personal interest or preference.  The LLM
still handles general semantic judgement in Stage 2.
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
    r"^(?:interest(?:ed)?|curious|preference|likes?|loves?|enjoys?|prefers?)\b",
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


def is_unsupported_user_interest_claim(
    *,
    fact: str,
    subjects: Sequence[str],
    evidence_excerpt: str,
    user_slug: str,
) -> bool:
    """Return whether a user-interest claim lacks an explicit user assertion.

    Topic choice is not evidence of personal interest.  The guard activates
    only for an interest/preference attitude about the current user; ownership,
    plans, events, and other fact families are deliberately untouched.
    """
    folded_fact = _fold(fact)
    candidate_probe = _INTEREST_RATE_RE.sub("", folded_fact)
    if not _CANDIDATE_INTEREST_RE.search(candidate_probe):
        return False

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
        return False

    expected_polarity = not bool(_NEGATIVE_CANDIDATE_RE.search(folded_fact))
    asserted = _asserted_interest_polarities(focus_user_evidence(evidence_excerpt))
    return expected_polarity not in asserted


__all__ = [
    "focus_user_evidence",
    "is_unsupported_user_interest_claim",
]
