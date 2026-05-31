"""Validator — rule-based second line of defense after the LLM extractor.

The LLM is usually good at subject disambiguation (the prompt is tight), but
**we do not rely on it alone**. All safety checks are repeated here so that
the system stays robust even when the LLM makes a mistake.

Three outcome categories:

- `accepted` — safe, can be merged directly (the Merger writes to the file).
- `review`   — suspicious or contradictory → review queue (UI badge).
- `rejected` — Do-Not-Record violation, subject confusion, or confidence too low.

The Validator does **not** write anything itself — it only sorts. The Merger
picks up `accepted` candidates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..people import PersonStore
from ..user_profile import UserProfile
from .extractor import Candidate

log = logging.getLogger(__name__)


# Minimum confidence for a direct merge. Anything below goes to review.
CONFIDENCE_ACCEPT = 0.7
CONFIDENCE_REVIEW = 0.5

# Minimum confidence required to overwrite an existing value.
# Stricter than the standard threshold — we do not want an LLM slip to change a name.
CONFIDENCE_OVERWRITE = 0.85


# Keywords that signal "Do Not Record" categories.
# Redundant with the prompt — belt-and-suspenders defence against LLM slips.
DO_NOT_RECORD_KEYWORDS = {
    # Politik / Religion
    "partei", "wähle", "waehle", "abtreibung", "migration", "christ", "muslim",
    "jude", "islam", "katholisch", "evangelisch", "atheist", "gott ",
    # Gesundheit / Mental Health (kurze Trigger, context-sensitive)
    "depression", "burnout", "angststörung", "angststoerung", "therapie",
    "adhs", "autismus", "diagnose", "diagnostiziert", "medikament",
    "antidepress", "psychiater", "krebs", "chronisch",
    # Finanz-Details (konkrete Zahlen)
    "euro verdien", "gehalt von", "mein einkommen",
    # MBTI & Pseudo-Psych
    "intp", "intj", "infp", "enfp", "entj", "entp", "estj", "estp",
    "istp", "istj", "isfj", "isfp", "esfj", "esfp", "infj",
}


# German personal pronouns that must NEVER be accepted as a person name
PRONOUN_FALSE_POSITIVES = {
    "er", "sie", "es", "ich", "du", "wir", "ihr", "mich", "dich",
    "mir", "dir", "ihn", "ihm", "ihnen", "uns", "euch",
    "he", "she", "it", "they", "we", "you", "him", "her", "them",
}


@dataclass
class ValidationResult:
    accepted: list[Candidate] = field(default_factory=list)
    review: list[tuple[Candidate, str]] = field(default_factory=list)   # (cand, reason)
    rejected: list[tuple[Candidate, str]] = field(default_factory=list)  # (cand, reason)


class Validator:
    """Checks candidates against subject confusion, confidence, and Do-Not-Record rules."""

    def __init__(self, profile: UserProfile, people: PersonStore) -> None:
        self._profile = profile
        self._people = people

    def validate(self, candidates: list[Candidate]) -> ValidationResult:
        result = ValidationResult()
        user_name = self._profile.name

        for cand in candidates:
            verdict, reason = self._check(cand, user_name=user_name)
            if verdict == "accept":
                result.accepted.append(cand)
            elif verdict == "review":
                result.review.append((cand, reason))
            else:
                result.rejected.append((cand, reason))
                log.debug("Curator reject: %s — %s", cand, reason)
        return result

    # ------------------------------------------------------------------
    # Core check — all rules in order
    # ------------------------------------------------------------------

    def _check(self, cand: Candidate, *, user_name: str | None) -> tuple[str, str]:
        # 1. Confidence below review threshold → reject
        if cand.confidence < CONFIDENCE_REVIEW:
            return "reject", f"confidence {cand.confidence:.2f} < {CONFIDENCE_REVIEW}"

        # 2. Do-Not-Record: keyword scan in evidence + value
        haystack = f"{cand.evidence} {cand.value}".lower()
        for kw in DO_NOT_RECORD_KEYWORDS:
            if kw in haystack:
                return "reject", f"do-not-record keyword: '{kw}'"

        # 3. Subject sanity
        if cand.is_person:
            pname = (cand.person_name or "").strip()
            if not pname:
                return "reject", "leerer Personen-Name"
            if pname.lower() in PRONOUN_FALSE_POSITIVES:
                return "reject", f"'{pname}' ist ein Pronomen, kein Name"
            if len(pname) < 2:
                return "reject", f"Name zu kurz: '{pname}'"
            # The user themselves must not appear as a person entry
            if user_name and pname.lower() == user_name.lower():
                return "reject", f"subject=person:{pname} == user.name"
        else:
            # subject=user: sanity check for identity.name / preferred_address
            if cand.cluster == "identity" and cand.field in ("name", "preferred_address"):
                existing = self._profile.get("identity", cand.field)
                new_val = str(cand.value).strip()
                # If the name is already set and differs → high threshold
                if existing and str(existing).lower() != new_val.lower():
                    if cand.confidence < CONFIDENCE_OVERWRITE:
                        return "review", (
                            f"name-Ueberschreibung: '{existing}' → '{new_val}' "
                            f"bei confidence {cand.confidence:.2f} < {CONFIDENCE_OVERWRITE}"
                        )
                # If the new name collides with a known person name
                # → most likely an LLM slip (the Laura-as-the maintainer problem)
                person = self._people.find_by_alias(new_val)
                if person is not None:
                    return "reject", (
                        f"name-Kollision: '{new_val}' existiert bereits als "
                        f"people/{person.path.stem}.md — Subject-Confusion vermieden"
                    )

        # 4. Confidence-based sorting
        if cand.confidence >= CONFIDENCE_ACCEPT:
            # Contradiction check: if 'set' and the existing value differs
            if cand.operation == "set" and not cand.is_person:
                existing = self._profile.get(cand.cluster, cand.field)
                if _differs(existing, cand.value):
                    if cand.confidence < CONFIDENCE_OVERWRITE:
                        return "review", (
                            f"Ueberschreibt '{existing}' → '{cand.value}' "
                            f"(conf {cand.confidence:.2f})"
                        )
            return "accept", ""

        # 5. Between review and accept threshold → review queue
        return "review", f"confidence {cand.confidence:.2f} < {CONFIDENCE_ACCEPT}"


def _differs(existing, new_value) -> bool:
    """Returns True if the new value differs meaningfully from the existing one."""
    if existing is None:
        return False
    if isinstance(existing, list) and isinstance(new_value, list):
        return set(map(str, existing)) != set(map(str, new_value))
    return str(existing).strip().lower() != str(new_value).strip().lower()
