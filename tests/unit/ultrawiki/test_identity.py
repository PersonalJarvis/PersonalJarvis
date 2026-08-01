"""Identity resolution logic — normalization, similarity, tiers.

Pure functions only: no database, no network, no model, no clock. The
calibration tests are the interesting ones — they pin the two families of
near-names the store must treat differently (speech-to-text variants of ONE
thing versus two genuinely different people) against the actual threshold, so
a future tuning change cannot quietly turn a proposal into a merge.
"""

from __future__ import annotations

import pytest

from jarvis.ultrawiki.identity import (
    DETERMINISTIC_KINDS,
    LEN_WINDOW,
    MAX_NAME_CHARS,
    MERGEABLE_TIERS,
    MIN_NAME_CHARS,
    MIN_PHONE_DIGITS,
    NICKNAME_SCORE,
    PREFIX_BLOCK_CHARS,
    PROPOSE_THRESHOLD,
    EntityKind,
    IdentifierKind,
    MatchEvidence,
    MatchTier,
    QueueStatus,
    could_match,
    escape_like,
    name_similarity,
    nickname_score,
    normalize_contact_slug,
    normalize_email,
    normalize_handle,
    normalize_identifier,
    normalize_name,
    normalize_phone,
    pair_key,
    tier_for_score,
)
from jarvis.ultrawiki.projection import MAX_LABEL_CHARS, MIN_LABEL_CHARS

# ---------------------------------------------------------------------------
# Value sets
# ---------------------------------------------------------------------------


def test_only_unique_identifier_kinds_are_deterministic():
    """A name never decides anything on its own — that is the whole design."""
    assert DETERMINISTIC_KINDS == {
        IdentifierKind.EMAIL,
        IdentifierKind.PHONE,
        IdentifierKind.CONTACT,
    }
    assert IdentifierKind.NAME not in DETERMINISTIC_KINDS
    assert IdentifierKind.HANDLE not in DETERMINISTIC_KINDS


def test_weak_evidence_can_never_become_a_merge():
    assert MatchTier.WEAK not in MERGEABLE_TIERS
    assert MERGEABLE_TIERS == {MatchTier.DETERMINISTIC, MatchTier.PROBABLE}


def test_name_bounds_match_the_projection_layer():
    """One corpus, one idea of what a usable label is."""
    assert (MIN_NAME_CHARS, MAX_NAME_CHARS) == (MIN_LABEL_CHARS, MAX_LABEL_CHARS)


def test_enum_values_are_stable_wire_strings():
    assert {kind.value for kind in EntityKind} == {
        "person",
        "place",
        "org",
        "project",
        "topic",
    }
    assert {status.value for status in QueueStatus} == {
        "pending",
        "confirmed",
        "rejected",
    }


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Viktoria   Novak ", "viktoria novak"),
        ("VIKTORIA NOVAK", "viktoria novak"),
        ("Novak, Viktoria.", "novak, viktoria"),
        ("-ultra-wiki-", "ultra-wiki"),
        ("a", None),  # below the floor
        ("x" * (MAX_NAME_CHARS + 1), None),  # above the ceiling
        ("...", None),
        (None, None),
        (42, None),
    ],
)
def test_normalize_name_cases(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_name_folds_decomposed_unicode():
    """macOS hands text back decomposed; without NFC the same name becomes
    two entities."""
    composed = "Zoë"  # Zoe with diaeresis, single code point
    decomposed = "Zoë"  # e + combining diaeresis
    assert composed != decomposed
    assert normalize_name(composed) == normalize_name(decomposed)


def test_name_normalization_stays_conservative():
    """The three spellings a speech-to-text pass produces of one title must
    remain THREE keys. Collapsing them here would be an implicit merge on name
    similarity — exactly what the design forbids."""
    keys = {
        normalize_name("Ultra Wiki"),
        normalize_name("ultra-wiki"),
        normalize_name("UltraWiki"),
    }
    assert len(keys) == 3
    # Case alone is not a difference, though.
    assert normalize_name("Ultra-Wiki") == normalize_name("ultra-wiki")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Person@Example.COM", "person@example.com"),
        ("  <person@example.com> ", "person@example.com"),
        ("mailto:person@example.com", "person@example.com"),
        ("not-an-email", None),
        ("a@b", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_email_cases(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+49 151 2345-6789", "+4915123456789"),
        ("0049 151 23456789", "+4915123456789"),
        ("(030) 12 34 56", "030123456"),
        ("12345", None),  # too short to be a unique identifier
        ("no digits", None),
        (None, None),
    ],
)
def test_normalize_phone_cases(raw, expected):
    assert normalize_phone(raw) == expected


def test_phone_formats_of_one_number_collapse_onto_one_key():
    """Two sources formatting the same number differently is precisely what
    makes 'same phone number' a deterministic match."""
    assert normalize_phone("+49 151 2345 6789") == normalize_phone("0049-151-23456789")


def test_short_numbers_can_never_merge_anything():
    assert normalize_phone("1" * (MIN_PHONE_DIGITS - 1)) is None
    assert normalize_phone("1" * MIN_PHONE_DIGITS) is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@RL", "rl"),
        ("Slack:@RL", "slack:rl"),
        ("x", None),
        (":handle", None),
        (None, None),
    ],
)
def test_normalize_handle_cases(raw, expected):
    assert normalize_handle(raw) == expected


def test_normalize_contact_slug():
    assert normalize_contact_slug(" Viktoria-Novak ") == "viktoria-novak"
    assert normalize_contact_slug("") is None


def test_normalize_identifier_dispatches_and_refuses_unknown_kinds():
    assert normalize_identifier(IdentifierKind.EMAIL, "A@B.co") == "a@b.co"
    assert normalize_identifier("phone", "+49 151 234567") == "+49151234567"
    assert normalize_identifier("nonsense", "value") is None


# ---------------------------------------------------------------------------
# Similarity calibration — the load-bearing numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Speech-to-text variants of ONE topic (observed in the live vault).
        ("agentic-i", "gentic-ide"),
        ("ultra wiki", "ultra-wiki"),
        ("ultrawiki", "ultra-wiki"),
        # Token order differences.
        ("viktoria novak", "novak viktoria"),
    ],
)
def test_near_duplicates_reach_the_proposal_threshold(left, right):
    assert name_similarity(left, right) >= PROPOSE_THRESHOLD
    assert tier_for_score(name_similarity(left, right)) is MatchTier.PROBABLE


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Two different people who merely share a surname.
        ("john smith", "jane smith"),
        # Nothing in common at all.
        ("agentic-i", "quarterly budget"),
    ],
)
def test_unrelated_names_stay_below_the_threshold(left, right):
    assert name_similarity(left, right) < PROPOSE_THRESHOLD
    assert tier_for_score(name_similarity(left, right)) is MatchTier.WEAK


def test_identical_names_score_one_but_are_still_not_deterministic():
    assert name_similarity("john smith", "john smith") == 1.0
    # A score can only ever propose. Deterministic is reserved for identifiers.
    assert tier_for_score(1.0) is MatchTier.PROBABLE


def test_nickname_rule_covers_the_canonical_example():
    """"Viki" is not similar to "Viktoria Novak" by any string metric, yet it
    is the textbook probable match."""
    assert nickname_score("viki", "viktoria novak") == NICKNAME_SCORE
    assert name_similarity("viki", "viktoria novak") >= PROPOSE_THRESHOLD


@pytest.mark.parametrize(
    ("short", "long"),
    [
        ("ana", "anastasia"),  # below the length floor
        ("rick", "patrick"),  # different first character
        ("vi ki", "viktoria novak"),  # a multi-word "nickname" is not one
        ("marc", "marcelinodefigueroa"),  # dwarfed by the long form
    ],
)
def test_nickname_rule_refuses_loose_abbreviations(short, long):
    assert nickname_score(short, long) == 0.0


def test_similarity_is_symmetric_and_bounded():
    for left, right in (("viki", "viktoria"), ("john smith", "jane smith")):
        score = name_similarity(left, right)
        assert score == name_similarity(right, left)
        assert 0.0 <= score <= 1.0


def test_empty_names_never_match():
    assert name_similarity("", "anything") == 0.0
    assert not could_match("", "anything")


# ---------------------------------------------------------------------------
# Blocking, keys, escaping
# ---------------------------------------------------------------------------


def test_could_match_mirrors_the_sql_candidate_block():
    """The SQL fetches by length window OR shared prefix; the Python gate must
    accept exactly those, or a candidate the database returned gets silently
    dropped by a rule the database never knew about."""
    base = "agentic ide"
    assert could_match(base, "a" * (len(base) + LEN_WINDOW))
    assert not could_match(base, "a" * (len(base) + LEN_WINDOW + 1))
    prefixed = base[:PREFIX_BLOCK_CHARS] + "x" * 40
    assert could_match(base, prefixed)


def test_pair_key_is_order_independent():
    assert pair_key(7, 3) == pair_key(3, 7) == "3:7"


def test_escape_like_neutralizes_wildcards():
    assert escape_like("100%_a\\b") == "100\\%\\_a\\\\b"


def test_match_evidence_round_trips_through_json_shape():
    evidence = MatchEvidence(
        tier=MatchTier.PROBABLE, kind="name_similar", value="viki", score=0.8412345
    )
    payload = evidence.to_dict()
    assert payload == {
        "tier": "probable",
        "kind": "name_similar",
        "value": "viki",
        "score": 0.8412,
    }
    assert MatchEvidence.from_dict(payload).kind == "name_similar"
