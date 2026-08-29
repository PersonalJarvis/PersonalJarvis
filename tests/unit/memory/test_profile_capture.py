"""Tests for the deterministic USER.md profile capture.

The load-bearing test here is NOT that the patterns fire — it is
``test_ordinary_conversation_writes_nothing``. A missed fact costs nothing;
a wrongly captured one writes a lie into the user's own profile and they have
to find and correct it. Precision is the contract, so the negative corpus is
deliberately larger than the positive one and includes the shapes that look
closest to a self-statement without being one.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.memory.profile_capture import (
    apply_profile_facts,
    capture_from_utterance,
    extract_profile_facts,
)
from jarvis.memory.user_profile import UserProfile
from jarvis.plugins.tool.profile_update import _CANONICAL_FIELDS

# ----------------------------------------------------------------------
# Extraction — the shapes people actually use
# ----------------------------------------------------------------------

POSITIVE: tuple[tuple[str, str, str, object], ...] = (
    # utterance, cluster, field, expected value
    ("Ich heiße Ruben.", "identity", "name", "Ruben"),
    ("Mein Name ist Ruben Lütke.", "identity", "name", "Ruben Lütke"),
    ("My name is Ruben.", "identity", "name", "Ruben"),
    ("Nenn mich einfach Chef.", "identity", "preferred_address", "Chef"),
    ("Du kannst mich Chef nennen.", "identity", "preferred_address", "Chef"),
    ("Call me Chef.", "identity", "preferred_address", "Chef"),
    ("Meine Pronomen sind er/ihm.", "identity", "pronouns", "er/ihm"),
    ("My pronouns are they/them.", "identity", "pronouns", "they/them"),
    ("Meine Zeitzone ist Europe/Berlin.", "identity", "timezone", "Europe/Berlin"),
    ("My timezone is Europe/Berlin.", "identity", "timezone", "Europe/Berlin"),
    ("Ich spreche auch Spanisch.", "identity", "languages", "es"),
    ("I also speak Spanish.", "identity", "languages", "es"),
    ("Sprich bitte Englisch mit mir.", "identity", "primary_language", "en"),
    ("Let's speak English.", "identity", "primary_language", "en"),
    ("Keine Emojis bitte.", "communication", "emoji_ok", False),
    ("No emojis, please.", "communication", "emoji_ok", False),
    ("Emojis sind ok.", "communication", "emoji_ok", True),
    ("Sei ruhig direkt.", "communication", "directness", "direct"),
    ("Be blunt with me.", "communication", "directness", "direct"),
    ("Fass dich kurz.", "communication", "verbosity", "short"),
    ("Keep it short.", "communication", "verbosity", "short"),
    ("Duz mich.", "communication", "formality", "casual"),
    ("Sieze mich.", "communication", "formality", "formal"),
    ("Mich nervt es, wenn man um den heißen Brei redet.", "values", "pet_peeves", None),
    ("I hate it when people are vague.", "values", "pet_peeves", None),
    ("Gib mir Feedback bitte immer direkt.", "relationship", "feedback_pref", "direct"),
    ("Stör mich nicht, wenn ich fokussiert bin.", "work_style", "focus_mode", "deep-work"),
)


@pytest.mark.parametrize(("utterance", "cluster", "field", "value"), POSITIVE)
def test_recognises_explicit_self_statements(
    utterance: str, cluster: str, field: str, value: object
) -> None:
    facts = extract_profile_facts(utterance)
    hits = [f for f in facts if f.cluster == cluster and f.field == field]
    assert hits, f"no {cluster}.{field} extracted from {utterance!r} (got {facts!r})"
    if value is not None:
        assert hits[0].value == value
    # Every fact carries the fragment it was read from — a write is never
    # anonymous in the log or on the ProfileUpdated event.
    assert hits[0].evidence


# Sentences that must produce NOTHING. Several are deliberately adjacent to a
# real self-statement: a question about the same topic, a fact about someone
# else, a hypothetical, a request that merely mentions a name.
NEGATIVE: tuple[str, ...] = (
    "",
    "   ",
    "Wie heißt du eigentlich?",
    "Wie ist deine Zeitzone?",
    "What is my name?",
    "Sie heißt Laura.",
    "Mein Bruder heißt Jonas.",
    "Nenn mich mal.",
    "Call me back later.",
    "Ruf mich später an.",
    "Wie spät ist es?",
    "Mach das Licht an.",
    "Schreib eine Mail an Anna.",
    "Was steht heute im Kalender?",
    "Erzähl mir einen Witz.",
    "Danke, das war super.",
    "Kannst du das nochmal machen?",
    "Spiel Musik.",
    "Öffne den Browser.",
    "Das Wetter ist heute gut.",
    "Ich glaube, das ist ein Fehler im Code.",
    "Ich brauche noch Milch.",
    "Der Termin ist um drei.",
    "Hallo, wie geht es dir?",
)


@pytest.mark.parametrize("utterance", NEGATIVE)
def test_ordinary_conversation_writes_nothing(utterance: str) -> None:
    assert extract_profile_facts(utterance) == ()


def test_a_question_about_the_topic_is_never_a_fact() -> None:
    # The capture group would read "Ruben oder Rubén?" — a question mark in the
    # value means the user was asking, not stating.
    assert extract_profile_facts("Heiße ich Ruben oder Rubén?") == ()


def test_an_overlong_capture_is_discarded() -> None:
    long_tail = "x" * 200
    assert extract_profile_facts(f"Nenn mich {long_tail}") == ()


def test_privacy_categories_never_reach_the_profile() -> None:
    # The USER.md "Do Not Record" contract — same filter as update_profile.
    assert extract_profile_facts("Mich nervt es, wenn man über Politik redet.") == ()
    assert extract_profile_facts("I hate it when people discuss religion.") == ()


def test_contradictory_shapes_cannot_both_win() -> None:
    facts = extract_profile_facts("Keine Emojis, Emojis sind ok.")
    emoji = [f for f in facts if f.field == "emoji_ok"]
    assert len(emoji) == 1


def test_every_emitted_field_is_canonical() -> None:
    """No pattern may name a field the Knowledge matrix never renders."""
    for utterance, cluster, field, _value in POSITIVE:
        for fact in extract_profile_facts(utterance):
            assert fact.field in _CANONICAL_FIELDS[fact.cluster], (
                f"{fact.cluster}.{fact.field} is outside the canonical allow-list"
            )
        assert field in _CANONICAL_FIELDS[cluster]


# ----------------------------------------------------------------------
# Writing — through the real UserProfile
# ----------------------------------------------------------------------

FRONTMATTER = """---
schema_version: 1
subject_type: user
identity:
  name: null
  languages: []
communication:
  emoji_ok: null
values:
  pet_peeves: []
---

# About the user
"""


def _profile(tmp_path: Path) -> UserProfile:
    path = tmp_path / "USER.md"
    path.write_text(FRONTMATTER, encoding="utf-8")
    return UserProfile.load(path)


def test_capture_writes_the_fact_to_disk(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    written = asyncio.run(capture_from_utterance(profile, "Ich heiße Ruben."))

    assert [(f.cluster, f.field) for f in written] == [("identity", "name")]
    assert UserProfile.load(tmp_path / "USER.md").meta["identity"]["name"] == "Ruben"


def test_repeating_a_fact_writes_nothing_the_second_time(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    assert asyncio.run(capture_from_utterance(profile, "Nenn mich Chef."))
    # The UI subscribes to ProfileUpdated; a no-op turn must not flash it.
    assert asyncio.run(capture_from_utterance(profile, "Nenn mich Chef.")) == ()


def test_list_fields_append_instead_of_replacing(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    asyncio.run(capture_from_utterance(profile, "Ich spreche auch Spanisch."))
    asyncio.run(capture_from_utterance(profile, "Ich spreche auch Französisch."))

    languages = UserProfile.load(tmp_path / "USER.md").meta["identity"]["languages"]
    assert languages == ["es", "fr"]


def test_boolean_fields_store_a_real_bool(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    asyncio.run(capture_from_utterance(profile, "Keine Emojis bitte."))
    assert UserProfile.load(tmp_path / "USER.md").meta["communication"]["emoji_ok"] is False


def test_a_silent_turn_never_touches_the_file(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    profile = _profile(tmp_path)
    before = path.read_bytes()

    assert asyncio.run(capture_from_utterance(profile, "Wie spät ist es?")) == ()

    # Not even last_updated may move — a save on every turn would rewrite the
    # file continuously and make the "last change" stamp meaningless.
    assert path.read_bytes() == before


def test_bridge_captures_from_a_finished_turn(tmp_path: Path) -> None:
    """The wiring, end to end: a turn on the bus updates USER.md."""
    from jarvis.core.bus import EventBus
    from jarvis.core.events import ProfileUpdated, VoiceTurnCompleted
    from jarvis.memory.profile_capture import ProfileCaptureBridge

    profile = _profile(tmp_path)
    bus = EventBus()
    announced: list[tuple[str, str]] = []

    async def _record(event: ProfileUpdated) -> None:
        announced.append((event.cluster, event.field))

    bus.subscribe(ProfileUpdated, _record)
    bridge = ProfileCaptureBridge(bus=bus, profile=profile)
    bridge.start()

    async def _drive() -> None:
        await bus.publish(VoiceTurnCompleted(user_text="Nenn mich Chef.", jarvis_text="ok"))
        await bus.publish(VoiceTurnCompleted(user_text="Wie spät ist es?", jarvis_text="ok"))

    asyncio.run(_drive())

    assert UserProfile.load(tmp_path / "USER.md").meta["identity"]["preferred_address"] == "Chef"
    # Exactly one announcement — the question turn contributed nothing.
    assert announced == [("identity", "preferred_address")]

    bridge.stop()
    asyncio.run(bus.publish(VoiceTurnCompleted(user_text="Ich heiße Ruben.", jarvis_text="ok")))
    assert UserProfile.load(tmp_path / "USER.md").meta["identity"]["name"] is None


def test_one_unwritable_fact_does_not_lose_the_others(tmp_path: Path) -> None:
    from jarvis.memory.profile_capture import ProfileFact

    profile = _profile(tmp_path)
    facts = (
        ProfileFact("identity", "name", "Ruben", "ich heiße Ruben"),
        # A cluster UserProfile rejects — set() raises ValueError for it.
        ProfileFact("nonsense", "name", "x", "x"),
    )
    written = asyncio.run(apply_profile_facts(profile, facts))

    assert [(f.cluster, f.field) for f in written] == [("identity", "name")]
    assert UserProfile.load(tmp_path / "USER.md").meta["identity"]["name"] == "Ruben"
