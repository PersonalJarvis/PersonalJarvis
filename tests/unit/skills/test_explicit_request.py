"""Unit tests for ``jarvis.skills.explicit_request``.

The resolver is the deterministic form of the doctrine in
``autofire_policy``: the user naming a skill is a sanctioned path to ANY
skill class. Precision over recall — every test in the "misses" section is a
hard negative that must never resolve.

Lightweight fakes, no ``unittest.mock`` (CLAUDE.md testing convention).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.skills.explicit_request import resolve_explicit_skill_request
from jarvis.skills.match_eval import BAND_FIRE, SOURCE_TRIGGER


@dataclass
class _FakeSkill:
    name: str
    frontmatter: object | None = None
    state: str = "validated"
    body: str = ""
    resources: dict = field(default_factory=dict)


class _FakeRegistry:
    def __init__(self, names: list[str]) -> None:
        self._skills = [_FakeSkill(name=n) for n in names]

    def list_active(self) -> list[_FakeSkill]:
        return list(self._skills)


_REGISTRY = _FakeRegistry([
    "morning-routine",
    "deep-work-mode",
    "plugin-gmail",
    "cli-gcloud",
    "focus",
    "focus-pro",
])


# ---------------------------------------------------------------------------
# Hits
# ---------------------------------------------------------------------------


def test_german_use_verb_and_kebab_name() -> None:
    hit = resolve_explicit_skill_request(
        "nutz den skill morning-routine bitte", _REGISTRY  # i18n-allow
    )
    assert hit is not None
    skill, decision = hit
    assert skill.name == "morning-routine"
    assert decision.band == BAND_FIRE
    assert decision.source == SOURCE_TRIGGER


def test_spoken_name_without_hyphens_resolves() -> None:
    hit = resolve_explicit_skill_request(
        "Jarvis, starte den Skill Morning Routine", _REGISTRY  # i18n-allow
    )
    assert hit is not None
    assert hit[0].name == "morning-routine"


def test_namespace_prefix_may_be_dropped() -> None:
    hit = resolve_explicit_skill_request("use the gmail skill", _REGISTRY)
    assert hit is not None
    assert hit[0].name == "plugin-gmail"


def test_cli_prefix_may_be_dropped() -> None:
    hit = resolve_explicit_skill_request("run the gcloud skill", _REGISTRY)
    assert hit is not None
    assert hit[0].name == "cli-gcloud"


def test_longest_spoken_name_wins() -> None:
    """"focus pro" must never resolve to the shorter skill "focus"."""
    hit = resolve_explicit_skill_request(
        "execute the skill focus pro now", _REGISTRY
    )
    assert hit is not None
    assert hit[0].name == "focus-pro"


def test_umlaut_verb_form_resolves() -> None:
    hit = resolve_explicit_skill_request(
        "führe den skill deep-work-mode aus", _REGISTRY  # i18n-allow
    )
    assert hit is not None
    assert hit[0].name == "deep-work-mode"


# ---------------------------------------------------------------------------
# Misses — every one of these firing would be an over-fire
# ---------------------------------------------------------------------------


def test_listing_question_has_no_use_verb() -> None:
    assert resolve_explicit_skill_request(
        "welche skills habe ich eigentlich", _REGISTRY  # i18n-allow
    ) is None


def test_authoring_request_names_no_installed_skill() -> None:
    assert resolve_explicit_skill_request(
        "erstell mir einen skill der jeden morgen die mails vorliest",  # i18n-allow
        _REGISTRY,
    ) is None


def test_missing_skill_word_stays_with_the_other_channels() -> None:
    """"starte die morning routine" is trigger/relevance territory."""
    assert resolve_explicit_skill_request(
        "starte die morning routine", _REGISTRY  # i18n-allow
    ) is None


def test_unknown_name_resolves_nothing() -> None:
    assert resolve_explicit_skill_request(
        "run the skill quarterly-report", _REGISTRY
    ) is None


def test_partial_namespaced_name_does_not_match() -> None:
    """"plugin" alone is not a name; only the full or prefix-dropped form is."""
    assert resolve_explicit_skill_request(
        "use the plugin skill", _REGISTRY
    ) is None


def test_empty_and_none_inputs() -> None:
    assert resolve_explicit_skill_request("", _REGISTRY) is None
    assert resolve_explicit_skill_request("use the gmail skill", None) is None


def test_informational_questions_never_execute() -> None:
    """Review finding 2026-08-12: "how do I use the X skill?" satisfies the
    verb + skill-word + name gates exactly like a command, but running the
    skill (worst case: dispatching a background mission) would be the wrong
    answer. The question-opener veto must catch every phrasing shape.
    """
    questions = [
        "how do I use the gmail skill?",
        "what does the skill morning-routine do",
        "wofür benutzt man den skill morning-routine",  # i18n-allow
        "wie nutze ich den skill deep-work-mode",  # i18n-allow
        "warum startet der skill morning-routine nicht",  # i18n-allow
        "hey jarvis, how do I run the gcloud skill",
    ]
    for utterance in questions:
        assert resolve_explicit_skill_request(utterance, _REGISTRY) is None, utterance


def test_polite_modal_requests_still_resolve() -> None:
    """"kannst du … nutzen" is a command in politeness clothing — it must NOT
    be caught by the question veto (only information openers veto).
    """
    hit = resolve_explicit_skill_request(
        "kannst du den skill morning routine nutzen", _REGISTRY  # i18n-allow
    )
    assert hit is not None
    assert hit[0].name == "morning-routine"

    hit = resolve_explicit_skill_request(
        "can you run the gmail skill for me", _REGISTRY
    )
    assert hit is not None
    assert hit[0].name == "plugin-gmail"
