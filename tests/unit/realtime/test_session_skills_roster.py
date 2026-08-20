"""The live session must know the names of the skills it can run.

Live forensic 2026-08-20, three turns in one call:

* "Morgenroutine" — the guard refused ``run-skill`` outright;
* "Morning-Routine" — the model answered "Morning Routine ist gestartet" with
  an empty tool-call list, so nothing ran and the user was told it had;
* "Okay, kannst du bitte mal ein Skill Morning Routine starten?" —
  ``run-skill`` ran and returned ``Unknown skill: Morning Routine``.

The realtime instructions were 17 488 characters long and contained the word
"skill" exactly zero times. The model held the tool and no catalogue, so the
argument was always a guess. The brain path had carried an ``AVAILABLE SKILLS``
section for months and a test to prove it; the realtime path had neither, and
every realtime transport shares this one instruction builder.

These tests are the drift guard for the roster reaching the prompt. Where the
skill is INJECTED once matched lives in ``test_session_skill_directive.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.realtime import session as session_module
from jarvis.skills import relevance
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.skill_context import SkillContext, set_skill_context


class _StubRunner:
    def render_instructions(self, skill: Any, *, args: dict | None = None) -> str:
        return "body"


class _Session:
    """Only the collaborators the roster methods touch."""

    def __init__(self, *, compact: bool = False, brain: Any = None) -> None:
        self._bus = None
        self._brain = brain
        self._compact_instructions = compact
        self._skill_inlined_for: tuple[str, str] | None = None
        self._skill_decision_cache = None
        self._config = None
        self.session_id = "roster-test"

    _skills_directive = session_module.RealtimeVoiceSession._skills_directive
    _note_skill_for_delegate = (
        session_module.RealtimeVoiceSession._note_skill_for_delegate
    )
    # The one skill evaluation per utterance the directives read.
    _skill_decision = session_module.RealtimeVoiceSession._skill_decision
    _skills_cfg = session_module.RealtimeVoiceSession._skills_cfg


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "test skill",
    tags: str = "",
) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'schema_version: "1"',
                f"name: {name}",
                f"description: {description}",
                *([f"tags: {tags}"] if tags else []),
                "---",
                "",
                "## Body",
                "",
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clean():
    relevance.clear_index_cache()
    set_skill_context(None)
    yield
    set_skill_context(None)
    relevance.clear_index_cache()


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root, "morning-routine", description="Your day at a glance.")
    _write_skill(root, "deep-work-mode", description="A focus sprint.")
    return root


def _install(root: Path) -> SkillRegistry:
    registry = SkillRegistry(root=root)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_StubRunner()))  # type: ignore[arg-type]
    return registry


# ---------------------------------------------------------------------------
# The roster reaches the instructions
# ---------------------------------------------------------------------------


def test_the_session_instructions_name_every_installed_skill(
    skills_root: Path,
) -> None:
    """The regression that started all of this: a prompt with the tool and no
    names. If this ever reads False again, the model is guessing."""
    _install(skills_root)

    instructions = session_module._session_instructions(
        "de",
        provider="vertex-live",
        model="gemini-live-2.5-flash-native-audio",
        skills_directive=_Session()._skills_directive(),
    )

    assert "morning-routine" in instructions
    assert "deep-work-mode" in instructions
    assert "run-skill" in instructions


def test_the_compact_profile_also_names_the_skills(skills_root: Path) -> None:
    """A small self-hosted brain gets a shorter roster, never no roster —
    provider parity is not optional (CLAUDE.md §3)."""
    _install(skills_root)

    instructions = session_module._session_instructions(
        "de",
        provider="local-realtime",
        model="qwen2.5:7b",
        skills_directive=_Session(compact=True)._skills_directive(compact=True),
        compact=True,
    )

    assert "morning-routine" in instructions
    assert "deep-work-mode" in instructions


def test_no_skill_context_yields_no_roster_block() -> None:
    """Headless and mock boots have no registry; the prompt just loses the
    block rather than the call losing its instructions."""
    assert _Session()._skills_directive() == ""


def test_a_registry_fault_never_breaks_the_instructions(skills_root: Path) -> None:
    """A roster fault costs a skill call. A raised exception costs the call."""

    class _Exploding:
        def list_active(self):  # noqa: ANN202
            raise RuntimeError("registry is on fire")

    set_skill_context(SkillContext(registry=_Exploding(), runner=_StubRunner()))  # type: ignore[arg-type]

    assert _Session()._skills_directive() == ""


# ---------------------------------------------------------------------------
# A match that cannot be inlined still reaches the brain
# ---------------------------------------------------------------------------


class _RecordingBrain:
    def __init__(self) -> None:
        self.noted: list[tuple[str, str]] = []

    def note_skill_trigger(
        self, skill_name: str, *, content: str = "", source: str = "trigger"
    ) -> None:
        self.noted.append((skill_name, source))


def test_a_matched_skill_is_handed_to_the_delegated_brain_turn(
    skills_root: Path,
) -> None:
    """Both installed morning routines failed every inline condition — one over
    the body cap, one tool-backed — and the old code answered that with "".
    A FIRE match must reach the brain instead of vanishing."""
    _install(skills_root)
    brain = _RecordingBrain()

    _Session(brain=brain)._note_skill_for_delegate("morning-routine")

    assert brain.noted == [("morning-routine", "realtime_match")]


def test_an_already_inlined_skill_is_not_handed_over_twice(
    skills_root: Path,
) -> None:
    """One instruction set per turn — two competing ones guarantee an
    incoherent reply."""
    _install(skills_root)
    brain = _RecordingBrain()
    session = _Session(brain=brain)
    session._skill_inlined_for = ("morning-routine", "morning-routine")

    session._note_skill_for_delegate("morning-routine")

    assert brain.noted == []


def test_an_unmatched_turn_hands_over_nothing(skills_root: Path) -> None:
    _install(skills_root)
    brain = _RecordingBrain()

    _Session(brain=brain)._note_skill_for_delegate("what is the capital of Peru")

    assert brain.noted == []


def test_a_brain_without_the_hook_is_tolerated(skills_root: Path) -> None:
    """Echo and mock brains have no ``note_skill_trigger``; the turn still runs."""
    _install(skills_root)

    _Session(brain=object())._note_skill_for_delegate("morning-routine")


# ---------------------------------------------------------------------------
# NARROW candidates become a suggestion instead of silence
# ---------------------------------------------------------------------------


class _CfgSession(_Session):
    """Adds the config surface the candidate hint reads."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)

    def _has_pending_delegate_from_earlier_turn(self) -> bool:
        return False

    _skill_candidates_directive = (
        session_module.RealtimeVoiceSession._skill_candidates_directive
    )
    _skills_cfg = session_module.RealtimeVoiceSession._skills_cfg
    _skill_directive = session_module.RealtimeVoiceSession._skill_directive


#: Scores 0.756 against the fixture below, between its HINT floor (0.330) and
#: its FIRE floor (1.019) — i.e. exactly the band this feature is about.
NARROW_UTTERANCE = "ich brauche konzentration"  # i18n-allow: test input


def _narrow_root(tmp_path: Path) -> Path:
    """A corpus where a paraphrase scores but does not reach FIRE."""
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(
        root,
        "deep-work-mode",
        description=(
            "Activates a distraction-free focus sprint with a pomodoro timer."
        ),
        tags="[pomodoro, fokus, konzentration, ruhe]",  # i18n-allow: fixture data
    )
    for index in range(8):
        _write_skill(root, f"filler-{index}", description=f"unrelated topic {index}")
    return root


def test_a_narrow_match_is_offered_to_the_model(tmp_path: Path) -> None:
    """The scorer finds the right skill for a paraphrase far more often than it
    fires — measured six of ten on the shipped corpus, three fired. The brain
    path already turned that surplus into a suggestion; realtime discarded it."""
    _install(_narrow_root(tmp_path))

    hint = _CfgSession()._skill_candidates_directive(NARROW_UTTERANCE)

    assert "deep-work-mode" in hint
    assert "not a verdict" in hint


def test_an_empty_utterance_offers_nothing(tmp_path: Path) -> None:
    _install(_narrow_root(tmp_path))

    assert _CfgSession()._skill_candidates_directive("   ") == ""


def test_an_unrelated_turn_offers_nothing(tmp_path: Path) -> None:
    """Silence is the common case and must stay free."""
    _install(_narrow_root(tmp_path))

    assert _CfgSession()._skill_candidates_directive("danke dir") == ""  # i18n-allow


def test_a_fire_match_is_not_also_offered_as_a_candidate(
    skills_root: Path,
) -> None:
    """A FIRE match is inlined or handed to the brain. Adding the hint on top
    would put two instruction sets on one turn."""
    _install(skills_root)
    session = _CfgSession()

    # The builtin's own trigger vocabulary — a FIRE-band hit, not a paraphrase.
    assert session._skill_candidates_directive("morning-routine") == ""


def test_a_registry_fault_never_breaks_the_hint(skills_root: Path) -> None:
    class _Exploding:
        def list_active(self):  # noqa: ANN202
            raise RuntimeError("registry is on fire")

        def get(self, name):  # noqa: ANN202
            raise RuntimeError("registry is on fire")

    set_skill_context(SkillContext(registry=_Exploding(), runner=_StubRunner()))  # type: ignore[arg-type]

    assert _CfgSession()._skill_candidates_directive("anything at all") == ""
