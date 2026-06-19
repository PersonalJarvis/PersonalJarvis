"""Unit-Tests fuer `jarvis.memory.curator.validator.Validator`.

Fokus — der Validator ist die **letzte Verteidigung** gegen Subject-Confusion:

- Confidence-Thresholds: <0.5 → reject, 0.5-0.7 → review, >=0.7 → accept.
- Laura-Szenario: `subject="user", field="name", value="Laura"` bei
  existierendem `people/laura.md` → REJECT wegen Kollision.
- Pronoun-False-Positives (`person:er`, `person:sie`) → REJECT.
- Do-Not-Record-Keywords (Politik, Mental Health, MBTI) → REJECT.
- Overwrite-Schutz: bestehender Name + neuer Name bei conf<0.85 → REVIEW.
"""
from __future__ import annotations

from typing import Any

from jarvis.memory.curator.extractor import Candidate
from jarvis.memory.curator.validator import (
    CONFIDENCE_ACCEPT,
    CONFIDENCE_OVERWRITE,
    CONFIDENCE_REVIEW,
    Validator,
)


# ----------------------------------------------------------------------
# Helper-Factory fuer Test-Candidates
# ----------------------------------------------------------------------

def _user_cand(
    *,
    cluster: str = "identity",
    field: str = "name",
    value: Any = "Ruben",
    confidence: float = 0.9,
    operation: str = "set",
    evidence: str = "User: 'ich heisse Ruben'",
) -> Candidate:
    return Candidate(
        subject="user",
        cluster=cluster,
        field=field,
        value=value,
        operation=operation,
        confidence=confidence,
        evidence=evidence,
    )


def _person_cand(
    name: str,
    *,
    cluster: str = "identity",
    field: str = "profession",
    value: Any = "Designerin",
    confidence: float = 0.9,
    operation: str = "set",
    evidence: str = "User: 'meine Freundin ist Designerin'",
    relationship: str = "partner",
) -> Candidate:
    return Candidate(
        subject=f"person:{name}",
        cluster=cluster,
        field=field,
        value=value,
        operation=operation,
        confidence=confidence,
        evidence=evidence,
        relationship=relationship,
    )


# ======================================================================
# Confidence-Thresholds
# ======================================================================

class TestConfidenceThresholds:
    def test_confidence_below_review_threshold_is_rejected(
        self, validator: Validator
    ) -> None:
        """<0.5 → reject."""
        cand = _user_cand(confidence=0.3)
        result = validator.validate([cand])
        assert len(result.rejected) == 1
        assert cand in [c for c, _ in result.rejected]

    def test_confidence_in_review_band_goes_to_review(self, validator: Validator) -> None:
        """0.5 <= conf < 0.7 → review."""
        # Feld ohne Overwrite-Konflikt (pref wirkt nur bei name/preferred_address)
        cand = _user_cand(
            cluster="communication", field="verbosity", value="tldr", confidence=0.6
        )
        result = validator.validate([cand])
        assert len(result.review) == 1
        assert len(result.accepted) == 0
        assert len(result.rejected) == 0

    def test_confidence_at_accept_threshold_is_accepted(
        self, validator: Validator
    ) -> None:
        """conf >= 0.7 und kein Konflikt → accept."""
        cand = _user_cand(
            cluster="communication",
            field="verbosity",
            value="tldr",
            confidence=CONFIDENCE_ACCEPT,
        )
        result = validator.validate([cand])
        assert len(result.accepted) == 1

    def test_confidence_well_above_accept_threshold(self, validator: Validator) -> None:
        cand = _user_cand(confidence=0.95)
        result = validator.validate([cand])
        assert len(result.accepted) == 1


# ======================================================================
# Laura-Szenario — die CRUCIAL-Regression
# ======================================================================

class TestLauraScenario:
    """Wenn `people/laura.md` existiert, darf 'Laura' NICHT als user.name durchgehen."""

    def test_rejects_laura_as_user_name_when_laura_exists_as_person(
        self, validator: Validator, person_store
    ) -> None:
        # Setup: Laura liegt bereits als Person vor (z.B. frueher extrahiert)
        person_store.get_or_create("Laura", relationship="partner")

        # LLM slippt: behauptet Laura sei der User-Name
        cand = _user_cand(field="name", value="Laura", confidence=0.9)
        result = validator.validate([cand])

        assert len(result.rejected) == 1, f"Laura muss rejected werden: {result}"
        _, reason = result.rejected[0]
        assert "kollision" in reason.lower() or "name-koll" in reason.lower()

    def test_ruben_as_user_name_is_accepted_even_with_laura_person_present(
        self, validator: Validator, person_store
    ) -> None:
        """Gegenprobe: fremde Person-Namen blocken nicht den echten User-Namen."""
        person_store.get_or_create("Laura", relationship="partner")

        cand = _user_cand(field="name", value="Ruben", confidence=0.95)
        result = validator.validate([cand])
        assert len(result.accepted) == 1


# ======================================================================
# Pronoun-False-Positives
# ======================================================================

class TestPronounRejection:
    def test_rejects_er_as_person_name(self, validator: Validator) -> None:
        cand = _person_cand("er", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.rejected) == 1
        _, reason = result.rejected[0]
        assert "pronom" in reason.lower()

    def test_rejects_sie_as_person_name(self, validator: Validator) -> None:
        cand = _person_cand("sie", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.rejected) == 1

    def test_rejects_english_pronouns(self, validator: Validator) -> None:
        for p in ("he", "she", "they", "them"):
            cand = _person_cand(p, confidence=0.9)
            result = validator.validate([cand])
            assert len(result.rejected) == 1, f"Pronoun '{p}' muss rejected werden"


# ======================================================================
# Subject-Sanity — kurze/leere Namen
# ======================================================================

class TestSubjectSanity:
    def test_rejects_single_char_name(self, validator: Validator) -> None:
        cand = _person_cand("X", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.rejected) == 1
        _, reason = result.rejected[0]
        assert "kurz" in reason.lower() or "zu kurz" in reason.lower()

    def test_rejects_empty_person_name(self, validator: Validator) -> None:
        cand = _person_cand("", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.rejected) == 1

    def test_rejects_person_equal_to_user_name(self, validator: Validator, profile) -> None:
        """Wenn User "Ruben" heisst, darf subject=person:Ruben nicht akzeptiert werden."""
        profile.set("identity", "name", "Ruben")
        cand = _person_cand("Ruben", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.rejected) == 1


# ======================================================================
# Do-Not-Record-Keywords
# ======================================================================

class TestDoNotRecord:
    def test_rejects_political_party(self, validator: Validator) -> None:
        cand = _user_cand(
            cluster="values",
            field="observation",
            value="Sympathie fuer die Linkspartei",
            evidence="User: 'ich mag die Linkspartei'",
            confidence=0.9,
        )
        result = validator.validate([cand])
        assert len(result.rejected) == 1
        _, reason = result.rejected[0]
        assert "partei" in reason.lower()

    def test_rejects_mental_health_depression(self, validator: Validator) -> None:
        cand = _user_cand(
            cluster="values",
            field="observation",
            value="Hat Depression",
            evidence="User: 'ich habe Depression'",
            confidence=0.9,
        )
        result = validator.validate([cand])
        assert len(result.rejected) == 1

    def test_rejects_mbti_type(self, validator: Validator) -> None:
        cand = _user_cand(
            cluster="values",
            field="observation",
            value="INTJ",
            evidence="User: 'ich bin INTJ'",
            confidence=0.9,
        )
        result = validator.validate([cand])
        assert len(result.rejected) == 1

    def test_rejects_religion_keyword(self, validator: Validator) -> None:
        cand = _user_cand(
            cluster="values",
            field="observation",
            value="Katholisch",
            evidence="Ich bin katholisch erzogen.",
            confidence=0.9,
        )
        result = validator.validate([cand])
        assert len(result.rejected) == 1


# ======================================================================
# Overwrite-Schutz fuer identity.name / preferred_address
# ======================================================================

class TestOverwriteProtection:
    def test_existing_name_new_value_below_overwrite_threshold_goes_to_review(
        self, validator: Validator, profile
    ) -> None:
        """Bestehender Name 'Ruben', neuer Name 'Paul' mit conf=0.75 → REVIEW."""
        profile.set("identity", "name", "Ruben")

        cand = _user_cand(field="name", value="Paul", confidence=0.75)
        result = validator.validate([cand])

        assert len(result.review) == 1, f"Erwarte review, got: {result}"
        assert len(result.accepted) == 0
        _, reason = result.review[0]
        assert "ueberschreibung" in reason.lower() or "ruben" in reason.lower()

    def test_existing_name_new_value_at_overwrite_threshold_is_accepted(
        self, validator: Validator, profile
    ) -> None:
        """Bestehender Name + neuer Name bei conf=0.9 → accepted."""
        profile.set("identity", "name", "Ruben")

        cand = _user_cand(
            field="name",
            value="Paul",
            confidence=CONFIDENCE_OVERWRITE + 0.05,
        )
        result = validator.validate([cand])
        assert len(result.accepted) == 1

    def test_same_name_value_passes_without_flagging(
        self, validator: Validator, profile
    ) -> None:
        """Neuer Name == existierender Name → kein Konflikt."""
        profile.set("identity", "name", "Ruben")
        cand = _user_cand(field="name", value="Ruben", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.accepted) == 1

    def test_overwrite_protection_for_scalar_field_contradiction(
        self, validator: Validator, profile
    ) -> None:
        """Nicht-name-Skalare: bestehender Wert + neuer abweichender Wert bei conf<0.85 → review."""
        profile.set("communication", "verbosity", "tldr")

        cand = _user_cand(
            cluster="communication",
            field="verbosity",
            value="deep-dive",
            confidence=0.75,  # < 0.85 overwrite threshold
        )
        result = validator.validate([cand])
        assert len(result.review) == 1


# ======================================================================
# Sanity-Batch — mehrere Candidates gleichzeitig
# ======================================================================

class TestBatchValidation:
    def test_mixed_batch_sorts_correctly(
        self, validator: Validator, person_store
    ) -> None:
        """Ein Mix aus accept/review/reject landet in den richtigen Buckets."""
        person_store.get_or_create("Laura", relationship="partner")

        accepted_cand = _user_cand(
            cluster="communication", field="verbosity", value="tldr", confidence=0.9
        )
        rejected_cand = _user_cand(field="name", value="Laura", confidence=0.9)
        low_conf_cand = _user_cand(confidence=0.2)

        result = validator.validate([accepted_cand, rejected_cand, low_conf_cand])
        assert accepted_cand in result.accepted
        assert rejected_cand in [c for c, _ in result.rejected]
        assert low_conf_cand in [c for c, _ in result.rejected]

    def test_thresholds_match_constants(self) -> None:
        """Sanity: Module-Level-Konstanten haben erwartete Reihenfolge."""
        assert CONFIDENCE_REVIEW < CONFIDENCE_ACCEPT < CONFIDENCE_OVERWRITE
