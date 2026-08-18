"""Unit tests for ``jarvis.skills.authoring_request``.

The resolver states one rule: when the user asks to CREATE a skill, every
service they name is the skill's CONTENT, never a command — so a brand
mentioned inside the request must not capture the turn. Precision over
recall: every entry in the "misses" section is a hard negative.

Lightweight fakes, no ``unittest.mock`` (CLAUDE.md testing convention).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from jarvis.skills.authoring_request import (
    AUTHORING_SKILL_NAME,
    is_skill_authoring_request,
    resolve_skill_authoring_request,
)
from jarvis.skills.match_eval import BAND_FIRE, BAND_NONE, SOURCE_TRIGGER


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


#: The exact transcript of the 2026-08-18 17:51 voice turn that this module
#: exists for — verbatim, filler words and misheard "Lenya Tickets" included.
LIVE_UTTERANCE = (
    "Ich möchte, dass du mich bitte einen neuen Skill erstellst und zwar morgen "  # i18n-allow: transcript
    "routine, der soll immer unter früh um 6 Uhr ähm ähm getriggert werden, "  # i18n-allow: transcript
    "gesetzt wird jeden Morgen um 6 Uhr, wo all meine E-Mails, Lenya Tickets und "  # i18n-allow: transcript
    "ähm alle wichtigen Kalendereinträge ähm abgespielt werden und dann ein "  # i18n-allow: transcript
    "schönes Lied abgespielt wird mit YouTube Music zum Aufstehen und zwar ein "  # i18n-allow: transcript
    "Klassiker aus den 80er, wie Country Road oder so ETC immer was neues."  # i18n-allow: transcript
)


# ---------------------------------------------------------------------------
# Hits — the request is an authoring request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        LIVE_UTTERANCE,
        "erstell mir einen skill der abends das licht dimmt",  # i18n-allow: test input
        "bau mir bitte einen neuen skill für die morgenroutine",  # i18n-allow: test input
        "kannst du einen skill erstellen der jeden morgen meine mails vorliest",  # i18n-allow: test input
        "leg einen neuen skill an: fokusmodus",  # i18n-allow: test input
        "mach aus diesem workflow einen skill",  # i18n-allow: test input
        "erstell skill morgenroutine",  # i18n-allow: test input
        "Skill erstellen: Abendroutine mit Spotify",  # i18n-allow: test input
        "dass er einen skill erstellt der abends das licht dimmt",  # i18n-allow: test input
        "create a skill that reads my calendar every morning",
        "turn this into a skill",
        "make me a new skill for the evening",
        "set up a skill for my monday review",
        "crea un skill que lea mi correo",  # i18n-allow: test input
    ],
)
def test_authoring_requests_are_detected(utterance: str) -> None:
    assert is_skill_authoring_request(utterance) is True


# ---------------------------------------------------------------------------
# Misses — hard negatives that must never resolve
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        # run / use / toggle / delete an existing skill
        "nutz den skill morning-routine bitte",  # i18n-allow: test input
        "starte den skill deep-work-mode",  # i18n-allow: test input
        "mach den skill deep-work-mode aus",  # i18n-allow: test input
        "lösch den skill abendroutine",  # i18n-allow: test input
        "starte den skill den du gestern erstellt hast",  # i18n-allow: test input
        "run the skill I created yesterday",
        # questions about skills
        "welche skills habe ich",  # i18n-allow: test input
        "wie erstelle ich einen skill",  # i18n-allow: test input
        "was ist ein skill",  # i18n-allow: test input
        "how do I create a skill?",
        # the skills only in a genitive / "of my" — a request ABOUT skills
        "erstell mir eine übersicht meiner skills",  # i18n-allow: test input
        "erstell mir eine liste aller skills",  # i18n-allow: test input
        "make a list of my skills",
        # no skill word at all — ordinary domain requests stay with their skills
        "erstell mir eine mail an das team",  # i18n-allow: test input
        "spiel musik auf youtube music",  # i18n-allow: test input
        "schreib eine mail an das team",  # i18n-allow: test input
        "",
    ],
)
def test_non_authoring_requests_are_ignored(utterance: str) -> None:
    assert is_skill_authoring_request(utterance) is False


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolves_to_the_active_skill_creator_with_trigger_grade_rights() -> None:
    registry = _FakeRegistry(["plugin-youtube_music", AUTHORING_SKILL_NAME, "morning-routine"])
    resolution = resolve_skill_authoring_request(LIVE_UTTERANCE, registry)
    assert resolution is not None
    assert resolution.skill is not None
    assert resolution.skill.name == AUTHORING_SKILL_NAME
    assert resolution.decision.band == BAND_FIRE
    assert resolution.decision.source == SOURCE_TRIGGER
    assert resolution.decision.top is not None
    assert resolution.decision.top.skill_name == AUTHORING_SKILL_NAME


def test_resolves_to_nobody_when_skill_creator_is_not_active() -> None:
    """The protection does not depend on the builtin: a disabled skill-creator
    still means NO other skill may capture, and the create-skill tool owns it."""
    registry = _FakeRegistry(["plugin-youtube_music", "morning-routine"])
    resolution = resolve_skill_authoring_request(LIVE_UTTERANCE, registry)
    assert resolution is not None
    assert resolution.skill is None
    assert resolution.decision.band == BAND_NONE
    assert resolution.decision.top is None


def test_returns_none_for_a_non_authoring_request() -> None:
    registry = _FakeRegistry([AUTHORING_SKILL_NAME])
    assert resolve_skill_authoring_request("spiel musik auf youtube music", registry) is None  # i18n-allow: test input


def test_never_raises_on_a_broken_registry() -> None:
    class _Broken:
        def list_active(self):  # noqa: ANN202
            raise RuntimeError("boom")

    resolution = resolve_skill_authoring_request(LIVE_UTTERANCE, _Broken())
    assert resolution is not None
    assert resolution.skill is None
    assert resolve_skill_authoring_request(LIVE_UTTERANCE, None) is not None
