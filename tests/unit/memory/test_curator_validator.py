"""Unit tests for `jarvis.memory.curator.validator.Validator`.

Focus — the validator is the **last line of defense** against subject confusion:

- Confidence thresholds: <0.5 → reject, 0.5-0.7 → review, >=0.7 → accept.
- Fictional-contact scenario: a known contact must not become the user's name.
- Pronoun false positives (`person:er`, `person:sie`) → REJECT.
- Do-not-record keywords (politics, mental health, MBTI) → REJECT.
- Overwrite protection: existing name + new name at conf<0.85 → REVIEW.
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
# Helper factory for test candidates
# ----------------------------------------------------------------------

def _user_cand(
    *,
    cluster: str = "identity",
    field: str = "name",
    value: Any = "ExampleUser",
    confidence: float = 0.9,
    operation: str = "set",
    evidence: str = "User: 'My name is ExampleUser'",
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
    value: Any = "designer",
    confidence: float = 0.9,
    operation: str = "set",
    evidence: str = "User: 'My fictional partner is a designer'",
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
        # Field without an overwrite conflict (pref only applies to name/preferred_address)
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
        """conf >= 0.7 and no conflict → accept."""
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
# Fictional contact scenario — the crucial regression
# ======================================================================

class TestFictionalContactScenario:
    """A known fictional contact must not pass as the user's name."""

    def test_rejects_contact_as_user_name_when_contact_exists_as_person(
        self, validator: Validator, person_store
    ) -> None:
        person_store.get_or_create("ExampleContact", relationship="partner")

        # Simulate an LLM confusing the fictional contact with the user.
        cand = _user_cand(field="name", value="ExampleContact", confidence=0.9)
        result = validator.validate([cand])

        assert len(result.rejected) == 1, f"Contact must be rejected: {result}"
        _, reason = result.rejected[0]
        assert "kollision" in reason.lower() or "name-koll" in reason.lower()

    def test_example_user_name_is_accepted_with_contact_present(
        self, validator: Validator, person_store
    ) -> None:
        """Counter-check: unrelated person names don't block the real user name."""
        person_store.get_or_create("ExampleContact", relationship="partner")

        cand = _user_cand(field="name", value="ExampleUser", confidence=0.95)
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
        assert "pronoun" in reason.lower()

    def test_rejects_sie_as_person_name(self, validator: Validator) -> None:
        cand = _person_cand("sie", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.rejected) == 1

    def test_rejects_english_pronouns(self, validator: Validator) -> None:
        for p in ("he", "she", "they", "them"):
            cand = _person_cand(p, confidence=0.9)
            result = validator.validate([cand])
            assert len(result.rejected) == 1, f"Pronoun '{p}' must be rejected"


# ======================================================================
# Subject-Sanity — kurze/leere Namen
# ======================================================================

class TestSubjectSanity:
    def test_rejects_single_char_name(self, validator: Validator) -> None:
        cand = _person_cand("X", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.rejected) == 1
        _, reason = result.rejected[0]
        assert "too short" in reason.lower()

    def test_rejects_empty_person_name(self, validator: Validator) -> None:
        cand = _person_cand("", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.rejected) == 1

    def test_rejects_person_equal_to_user_name(self, validator: Validator, profile) -> None:
        """A person subject equal to the user's name must not be accepted."""
        profile.set("identity", "name", "ExampleUser")
        cand = _person_cand("ExampleUser", confidence=0.9)
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
            value="Sympathie fuer die Linkspartei",  # i18n-allow: reject fixture
            evidence="User: 'ich mag die Linkspartei'",  # i18n-allow: fixture
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
# Overwrite protection for identity.name / preferred_address
# ======================================================================

class TestOverwriteProtection:
    def test_existing_name_new_value_below_overwrite_threshold_goes_to_review(
        self, validator: Validator, profile
    ) -> None:
        """An existing name and a new value at confidence 0.75 require review."""
        profile.set("identity", "name", "ExampleUser")

        cand = _user_cand(field="name", value="Paul", confidence=0.75)
        result = validator.validate([cand])

        assert len(result.review) == 1, f"Expected review, got: {result}"
        assert len(result.accepted) == 0
        _, reason = result.review[0]
        assert "ueberschreibung" in reason.lower() or "exampleuser" in reason.lower()

    def test_existing_name_new_value_at_overwrite_threshold_is_accepted(
        self, validator: Validator, profile
    ) -> None:
        """An existing name plus a new name at confidence 0.9 is accepted."""
        profile.set("identity", "name", "ExampleUser")

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
        """New name == existing name → no conflict."""
        profile.set("identity", "name", "ExampleUser")
        cand = _user_cand(field="name", value="ExampleUser", confidence=0.9)
        result = validator.validate([cand])
        assert len(result.accepted) == 1

    def test_overwrite_protection_for_scalar_field_contradiction(
        self, validator: Validator, profile
    ) -> None:
        """Non-name scalars: existing value + new differing value at conf<0.85 → review."""
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
# Batch sanity — several candidates together
# ======================================================================

class TestBatchValidation:
    def test_mixed_batch_sorts_correctly(
        self, validator: Validator, person_store
    ) -> None:
        """A mix of accept/review/reject lands in the right buckets."""
        person_store.get_or_create("ExampleContact", relationship="partner")

        accepted_cand = _user_cand(
            cluster="communication", field="verbosity", value="tldr", confidence=0.9
        )
        rejected_cand = _user_cand(field="name", value="ExampleContact", confidence=0.9)
        low_conf_cand = _user_cand(confidence=0.2)

        result = validator.validate([accepted_cand, rejected_cand, low_conf_cand])
        assert accepted_cand in result.accepted
        assert rejected_cand in [c for c, _ in result.rejected]
        assert low_conf_cand in [c for c, _ in result.rejected]

    def test_thresholds_match_constants(self) -> None:
        """Module-level constants have the expected order."""
        assert CONFIDENCE_REVIEW < CONFIDENCE_ACCEPT < CONFIDENCE_OVERWRITE
