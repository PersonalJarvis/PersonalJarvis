"""Unit tests for the contacts ``relationship`` enum (Layer 0 source of truth).

The ``relationship`` field crosses Python ↔ Pydantic ↔ TypeScript ↔ UI label,
so it follows the five-layer anti-drift pattern (docs/anti-drift-three-layer.md).
This module tests the Python source of truth in ``jarvis/contacts/schema.py``;
the TS-mirror parity is in ``test_relationship_parity.py``.
"""
from __future__ import annotations

from typing import get_args

import pytest


def test_relationship_vocabulary_is_the_agreed_set() -> None:
    from jarvis.contacts.schema import RELATIONSHIPS

    assert set(RELATIONSHIPS) == {
        "family",
        "friend",
        "colleague",
        "partner",
        "acquaintance",
        "other",
    }


def test_literal_matches_tuple() -> None:
    # Import-time assertion already guards this; the explicit test documents it.
    from jarvis.contacts.schema import RELATIONSHIPS, Relationship

    assert set(get_args(Relationship)) == set(RELATIONSHIPS)


def test_symbolic_constants_exist_for_each_value() -> None:
    from jarvis.contacts import schema

    assert schema.REL_FAMILY == "family"
    assert schema.REL_FRIEND == "friend"
    assert schema.REL_COLLEAGUE == "colleague"
    assert schema.REL_PARTNER == "partner"
    assert schema.REL_ACQUAINTANCE == "acquaintance"
    assert schema.REL_OTHER == "other"


def test_normalize_accepts_canonical_and_lowercases() -> None:
    from jarvis.contacts.schema import normalize_relationship

    assert normalize_relationship("friend") == "friend"
    assert normalize_relationship("  Friend  ") == "friend"
    assert normalize_relationship("COLLEAGUE") == "colleague"


def test_normalize_none_and_empty_yield_none() -> None:
    from jarvis.contacts.schema import normalize_relationship

    assert normalize_relationship(None) is None
    assert normalize_relationship("") is None
    assert normalize_relationship("   ") is None


def test_normalize_rejects_unknown_value() -> None:
    from jarvis.contacts.schema import normalize_relationship

    with pytest.raises(ValueError):
        normalize_relationship("enemy")
