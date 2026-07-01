"""Phase 1c E2E: SkillRegistry -> TriggerMatcher -> SkillRunner -> Tool-Dispatch."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.bus import EventBus
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.runner import SkillRunner
from jarvis.skills.trigger_matcher import TriggerMatcher


@pytest.fixture
def builtin_root() -> Path:
    return Path(__file__).resolve().parents[2] / "jarvis" / "skills" / "builtin"


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def test_registry_loads_all_3_builtin_skills(builtin_root: Path, bus: EventBus) -> None:
    reg = SkillRegistry(builtin_root, bus)
    reg.reload_sync()
    names = [s.frontmatter.name for s in reg.list() if s.frontmatter is not None]
    assert "morning-routine" in names
    assert "deep-work-mode" in names
    assert "memory-save" in names


def test_trigger_matcher_matches_voice_de(builtin_root: Path, bus: EventBus) -> None:
    reg = SkillRegistry(builtin_root, bus)
    reg.reload_sync()
    matcher = TriggerMatcher(reg)
    skill = matcher.match_voice("guten morgen", lang="de")
    assert skill is not None
    assert skill.frontmatter is not None
    assert skill.frontmatter.name == "morning-routine"


def test_trigger_matcher_matches_voice_en(builtin_root: Path, bus: EventBus) -> None:
    reg = SkillRegistry(builtin_root, bus)
    reg.reload_sync()
    matcher = TriggerMatcher(reg)
    skill = matcher.match_voice("good morning", lang="en")
    assert skill is not None
    assert skill.frontmatter is not None
    assert skill.frontmatter.name == "morning-routine"


def test_trigger_matcher_hotkey_deep_work(builtin_root: Path, bus: EventBus) -> None:
    reg = SkillRegistry(builtin_root, bus)
    reg.reload_sync()
    matcher = TriggerMatcher(reg)
    skill = matcher.match_hotkey("ctrl+alt+d")
    assert skill is not None
    assert skill.frontmatter is not None
    assert skill.frontmatter.name == "deep-work-mode"


def test_memory_save_skill_is_disabled_and_does_not_trigger(
    builtin_root: Path, bus: EventBus
) -> None:
    """Regression guard: memory-save is deprecated (state=disabled, triggers=[]).

    The skill was hard-disabled in B5 (2026-05-13) because long-term-memory
    writes now flow through the wiki pipeline (Awareness -> SessionRollupWorker
    -> WikiCurator).  The TriggerMatcher must NOT match any voice phrase for it.
    If this test fails, the skill was accidentally re-enabled.
    """
    reg = SkillRegistry(builtin_root, bus)
    reg.reload_sync()
    matcher = TriggerMatcher(reg)
    skill = matcher.match_voice(
        "merk dir: python ist meine lieblings-sprache", lang="de"  # i18n-allow
    )
    # Disabled skill with empty triggers must never fire.
    assert skill is None


@pytest.mark.asyncio
async def test_skill_runner_instantiates_correctly(
    builtin_root: Path, bus: EventBus
) -> None:
    """Regression test for BLOCKER 2 (SkillRunner signature)."""
    reg = SkillRegistry(builtin_root, bus)
    reg.reload_sync()
    # Must work without TypeError after the fix
    runner = SkillRunner(registry=reg, bus=bus)
    assert runner is not None
    assert runner.registry is reg
    assert runner.bus is bus
