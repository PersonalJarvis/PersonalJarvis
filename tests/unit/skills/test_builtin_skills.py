"""Unit tests for the 3 built-in skills.

Checks that all SKILL.md files parse, have valid frontmatter,
all trigger payloads are semantically OK, and voice regexes compile.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# If parallel B1 work isn't finished yet: graceful skip.
pytest.importorskip("jarvis.skills.schema")
pytest.importorskip("jarvis.skills.loader")

from jarvis.skills.builtin import BUILTIN_SKILL_NAMES, builtin_skill_path
from jarvis.skills.loader import parse_skill
from jarvis.skills.schema import SkillFrontmatter, SkillLifecycleState


@pytest.mark.parametrize("name", list(BUILTIN_SKILL_NAMES))
def test_skill_file_exists(name: str) -> None:
    path = builtin_skill_path(name)
    assert path.is_file(), f"Missing SKILL.md for builtin '{name}' at {path}"


@pytest.mark.parametrize("name", list(BUILTIN_SKILL_NAMES))
def test_skill_parses_successfully(name: str) -> None:
    path = builtin_skill_path(name)
    skill = parse_skill(path)
    assert skill.frontmatter is not None, (
        f"parse_skill returned no frontmatter for {name}: error={skill.error}"
    )
    assert skill.state != SkillLifecycleState.DRAFT or skill.error is None
    # Explicitly validate the frontmatter model
    SkillFrontmatter.model_validate(skill.frontmatter.model_dump())


@pytest.mark.parametrize("name", list(BUILTIN_SKILL_NAMES))
def test_skill_has_valid_triggers(name: str) -> None:
    skill = parse_skill(builtin_skill_path(name))
    assert skill.frontmatter is not None
    # Disabled skills are intentionally trigger-less (e.g. memory-save was
    # deprecated in B5 once the wiki-ingest tool took over the same job)
    # and must NOT be auto-fired by the TriggerMatcher.  Loading them
    # remains valid so that historical references in user data still
    # resolve.
    if skill.frontmatter.state == SkillLifecycleState.DISABLED:
        assert skill.frontmatter.triggers == [], (
            f"disabled skill {name} must not declare triggers"
        )
        return
    # Meta skills (category="meta") may live without an auto trigger — they
    # are pulled by the supervisor via intent dispatch, not by the
    # TriggerMatcher. All other categories need at least one.
    if skill.frontmatter.category != "meta":
        assert len(skill.frontmatter.triggers) >= 1, (
            f"{name} needs at least one trigger (category={skill.frontmatter.category})"
        )
    for t in skill.frontmatter.triggers:
        errors = t.validate_payload()
        assert not errors, f"{name} trigger invalid: {errors}"


@pytest.mark.parametrize("name", list(BUILTIN_SKILL_NAMES))
def test_skill_has_risk_policy(name: str) -> None:
    skill = parse_skill(builtin_skill_path(name))
    assert skill.frontmatter is not None
    rp = skill.frontmatter.risk_policy
    assert rp.default_tier in ("safe", "monitor", "ask", "block")


@pytest.mark.parametrize("name", list(BUILTIN_SKILL_NAMES))
def test_voice_patterns_compile(name: str) -> None:
    skill = parse_skill(builtin_skill_path(name))
    assert skill.frontmatter is not None
    for t in skill.frontmatter.triggers:
        if t.type == "voice":
            assert t.pattern is not None
            # Must compile as a regex
            compiled = re.compile(t.pattern, re.IGNORECASE)
            assert compiled is not None


def test_expected_builtin_count() -> None:
    """The base skills (Phase 1c + skill-creator + control-api) plus the paired
    plugin skills. Computed from the components so adding a plugin does not rot
    this guard; only a missing base skill or a duplicate trips it."""
    from jarvis.skills.builtin import _PLUGIN_PAIRED_SKILLS

    base = {
        "morning-routine",
        "deep-work-mode",
        "memory-save",
        "skill-creator",
        "control-api",
        "cli-gcloud",
    }
    assert base.issubset(set(BUILTIN_SKILL_NAMES)), (
        f"missing base skill(s): {base - set(BUILTIN_SKILL_NAMES)}"
    )
    assert len(BUILTIN_SKILL_NAMES) == len(base) + len(_PLUGIN_PAIRED_SKILLS)
    assert len(set(BUILTIN_SKILL_NAMES)) == len(BUILTIN_SKILL_NAMES), "duplicate skill name"


def test_control_api_specifics() -> None:
    """The shipped Control-API skill is documentation for coding agents.

    AP-15: VALIDATED (not DRAFT — would never load; not ACTIVE — would bypass
    review). category=meta so it carries NO voice triggers: a trigger matching
    "switch language" would make the router pick run_skill (a markdown body that
    cannot call HTTP) instead of the set_config_value tool on the voice path.
    """
    skill = parse_skill(builtin_skill_path("control-api"))
    fm = skill.frontmatter
    assert fm is not None
    assert skill.state == SkillLifecycleState.VALIDATED
    assert fm.category == "meta"
    assert fm.triggers == []


def test_cli_gcloud_specifics() -> None:
    """The gcloud guidance skill teaches the brain to drive Google Cloud via the
    cli_gcloud tool instead of the browser console. Like control-api it is
    category=meta with NO voice triggers — a trigger would make the router pick
    run_skill (a markdown body) over the cli_gcloud tool. Gated to gcloud being
    connected via requires_tools; guidance-only (no paired capability, so no
    vocab duplication with the CLI catalog).
    """
    skill = parse_skill(builtin_skill_path("cli-gcloud"))
    fm = skill.frontmatter
    assert fm is not None
    assert skill.state == SkillLifecycleState.VALIDATED
    assert fm.category == "meta"
    assert fm.triggers == []
    assert fm.requires_tools == ["cli_gcloud"]
    assert fm.intent_verbs == []
    assert fm.intent_objects == []


def test_morning_routine_specifics() -> None:
    """Instruction-skill model (2026-06-09): no fictional MCP tool names in
    requires_tools — the body instructs the brain to use whatever calendar/
    mail/web tools are actually connected."""
    skill = parse_skill(builtin_skill_path("morning-routine"))
    fm = skill.frontmatter
    assert fm is not None
    trig_types = {t.type for t in fm.triggers}
    assert "voice" in trig_types
    assert "schedule" in trig_types
    assert fm.requires_tools == []
    assert fm.execution == "inline"
    assert fm.config.get("weather_location") == "Berlin"


def test_deep_work_mode_specifics() -> None:
    skill = parse_skill(builtin_skill_path("deep-work-mode"))
    fm = skill.frontmatter
    assert fm is not None
    trig_types = {t.type for t in fm.triggers}
    assert "hotkey" in trig_types
    assert "voice" in trig_types
    hotkey = next(t for t in fm.triggers if t.type == "hotkey")
    assert hotkey.combo == "ctrl+alt+d"
    assert fm.config.get("duration_minutes") == 90


def test_memory_save_specifics() -> None:
    """memory-save was deprecated in B5 once the wiki-ingest tool took over
    the same job.  The skill remains in the registry as DISABLED so old
    user-facing references resolve; it must not auto-fire from the
    TriggerMatcher.  The original trigger-shape assertions are replaced
    by a contract check: state=DISABLED, no triggers, risk_policy safe.
    """
    skill = parse_skill(builtin_skill_path("memory-save"))
    fm = skill.frontmatter
    assert fm is not None
    assert fm.state == SkillLifecycleState.DISABLED, (
        "memory-save must remain DISABLED — re-enable would re-introduce "
        "the BUG-class addressed by wiki-ingest"
    )
    assert fm.triggers == [], (
        "memory-save must have no triggers — TriggerMatcher must not fire it"
    )
    assert fm.risk_policy.default_tier == "safe"
