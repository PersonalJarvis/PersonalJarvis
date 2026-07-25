"""Direct skill injection into a live realtime turn — the latency fix.

A matched skill used to be reachable in realtime only through the delegate,
which BUG-087 measured at 9.6 s to first audio. A skill that needs no tools does
not need any of that: its instructions can ride the per-turn ``update_session``
that already fires on every final transcript, and the live model answers at
native speed.

The value of this path is entirely in its conditions, so that is what these
tests are. Every one of them describes a case that must fall back to the
delegate rather than being injected — because the failure mode of getting it
wrong is a model receiving instructions it cannot carry out, with no tool and no
way to say so.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.realtime import session as session_module
from jarvis.skills import relevance
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.skill_context import SkillContext, set_skill_context

# Speech input under test — measured to reach the FIRE band on this fixture.
FIRE_UTTERANCE = "aktiviere den fokus und den konzentrationsmodus"  # i18n-allow
FOCUS_TAGS = "[konzentrationsmodus, fokus]"  # i18n-allow


class _StubRunner:
    """Renders a fixed body, so the test controls exactly what gets injected."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.calls = 0

    def render_instructions(self, skill: Any, *, args: dict | None = None) -> str:
        self.calls += 1
        return self.body


class _Session:
    """The two collaborators ``_skill_directive`` actually touches.

    Bound rather than subclassed: constructing a real ``RealtimeVoiceSession``
    would pull in a provider, a transport and an audio pipeline, none of which
    this method knows about.
    """

    def __init__(self, *, pending: bool = False) -> None:
        self._bus = None
        self._pending = pending

    def _has_pending_delegate_from_earlier_turn(self) -> bool:
        return self._pending

    _skill_directive = session_module.RealtimeVoiceSession._skill_directive


def _write_skill(
    root: Path,
    name: str,
    *,
    tags: str | None = None,
    description: str = "test skill",
    execution: str | None = None,
    tier: str | None = None,
    requires_tools: str | None = None,
) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        'schema_version: "1"',
        f"name: {name}",
        f"description: {description}",
    ]
    if tags:
        lines.append(f"tags: {tags}")
    if execution:
        lines.append(f"execution: {execution}")
    if tier:
        lines += ["risk_policy:", f"  default_tier: {tier}"]
    if requires_tools:
        lines.append(f"requires_tools: {requires_tools}")
    lines += ["---", "", "## Body", ""]
    (folder / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def _install(root: Path, body: str = "Take a breath and start a 25 minute sprint.") -> _StubRunner:
    registry = SkillRegistry(root=root)
    registry.reload_sync()
    runner = _StubRunner(body)
    set_skill_context(SkillContext(registry=registry, runner=runner))  # type: ignore[arg-type]
    return runner


@pytest.fixture(autouse=True)
def _clean() -> None:
    relevance.clear_index_cache()
    set_skill_context(None)
    yield
    set_skill_context(None)
    relevance.clear_index_cache()


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root, "deep-work-mode", tags=FOCUS_TAGS)
    for index in range(6):
        _write_skill(root, f"filler-{index}", description=f"topic{index}")
    return root


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_tool_free_inline_skill_is_injected(skills_root: Path) -> None:
    runner = _install(skills_root)
    directive = _Session()._skill_directive(FIRE_UTTERANCE)

    assert directive
    assert '<skill name="deep-work-mode">' in directive
    assert runner.body in directive
    assert "Never read it aloud" in directive
    assert runner.calls == 1


def test_nothing_is_injected_without_a_match(skills_root: Path) -> None:
    _install(skills_root)
    assert _Session()._skill_directive("wie ist das wetter in berlin") == ""  # i18n-allow


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_input_injects_nothing(skills_root: Path, text: str) -> None:
    _install(skills_root)
    assert _Session()._skill_directive(text) == ""


def test_no_skill_context_injects_nothing() -> None:
    assert _Session()._skill_directive(FIRE_UTTERANCE) == ""


# ---------------------------------------------------------------------------
# Every condition that must fall back to the delegate
# ---------------------------------------------------------------------------


def test_a_mission_skill_is_never_injected(tmp_path: Path) -> None:
    """This path cannot dispatch a worker, so a mission skill must not use it."""
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root, "cloud-debug", tags=FOCUS_TAGS, execution="mission")
    for index in range(6):
        _write_skill(root, f"filler-{index}", description=f"topic{index}")
    _install(root)

    assert _Session()._skill_directive(FIRE_UTTERANCE) == ""


def test_a_tool_backed_skill_is_never_injected(tmp_path: Path) -> None:
    """It would need an integration the live session cannot reach."""
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root, "plugin-gmail", tags=FOCUS_TAGS, requires_tools="[gmail]")
    for index in range(6):
        _write_skill(root, f"filler-{index}", description=f"topic{index}")
    _install(root)

    assert _Session()._skill_directive(FIRE_UTTERANCE) == ""


def test_an_ask_tier_skill_is_never_injected(tmp_path: Path) -> None:
    """`ask` needs the voice-confirm machinery that lives in the orchestrator."""
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root, "risky", tags=FOCUS_TAGS, tier="ask")
    for index in range(6):
        _write_skill(root, f"filler-{index}", description=f"topic{index}")
    _install(root)

    assert _Session()._skill_directive(FIRE_UTTERANCE) == ""


def test_an_oversized_body_falls_back_instead_of_truncating(
    skills_root: Path,
) -> None:
    """The most important rule in this file.

    A half-injected instruction list produces a half-executed skill, which is
    strictly worse than a slow correct answer. So the cap causes a FALLBACK, and
    never a cut.
    """
    oversized = "x" * (session_module._REALTIME_SKILL_MAX_CHARS + 1)
    _install(skills_root, body=oversized)

    assert _Session()._skill_directive(FIRE_UTTERANCE) == ""


def test_a_body_just_under_the_cap_still_injects(skills_root: Path) -> None:
    body = "y" * (session_module._REALTIME_SKILL_MAX_CHARS - 10)
    _install(skills_root, body=body)

    directive = _Session()._skill_directive(FIRE_UTTERANCE)
    assert body in directive


def test_a_body_mentioning_tools_falls_back(skills_root: Path) -> None:
    """An author declaring no tools while writing "use the Gmail tool" is a
    plausible slip — and this session only has jarvis_action and end_call."""
    _install(skills_root, body="Open the inbox with the gmail tool and summarise.")

    assert _Session()._skill_directive(FIRE_UTTERANCE) == ""


def test_nothing_is_injected_while_a_delegate_is_pending(
    skills_root: Path,
) -> None:
    """Two competing instruction sets guarantee an incoherent reply."""
    _install(skills_root)
    assert _Session(pending=True)._skill_directive(FIRE_UTTERANCE) == ""


def test_a_broken_renderer_falls_back_silently(skills_root: Path) -> None:
    class _Exploding:
        def render_instructions(self, skill: Any, *, args: dict | None = None) -> str:
            raise RuntimeError("template is on fire")

    registry = SkillRegistry(root=skills_root)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_Exploding()))  # type: ignore[arg-type]

    assert _Session()._skill_directive(FIRE_UTTERANCE) == ""


# ---------------------------------------------------------------------------
# Instruction assembly
# ---------------------------------------------------------------------------


def test_the_directive_sits_below_the_safety_appendix() -> None:
    """Safety must still frame a skill's instructions, not the other way round."""
    instructions = session_module._session_instructions(
        "de",
        tool_directive="TOOLDIRECTIVE",
        skill_directive="SKILLDIRECTIVE",
    )
    assert "SKILLDIRECTIVE" in instructions
    assert instructions.index("TOOLDIRECTIVE") < instructions.index("SKILLDIRECTIVE")
    assert instructions.index("SKILLDIRECTIVE") < instructions.index(
        session_module._REALTIME_SAFETY_APPENDIX[:40]
    )


def test_omitting_the_directive_changes_nothing() -> None:
    """Every existing caller passes nothing — the parameter must be inert."""
    assert session_module._session_instructions(
        "de", tool_directive="X"
    ) == session_module._session_instructions("de", tool_directive="X", skill_directive="")


def test_the_cap_is_tighter_than_the_preferences_cap() -> None:
    """A skill body is less trusted and more variable than the user's own file."""
    assert (
        session_module._REALTIME_SKILL_MAX_CHARS
        < session_module._PREFERENCES_MAX_CHARS
    )
