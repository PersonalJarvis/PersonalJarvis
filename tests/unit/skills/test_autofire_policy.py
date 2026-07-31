"""Capture permission — who may take over a turn, and who never may.

A matched skill does not add to a turn, it takes it over: the body is injected,
``run-skill`` is removed so the model cannot decline, the local-action fast path
and the evidence gate stand down, and a mission skill dispatches a worker and
returns before the model sees anything. These tests pin the asymmetry that
follows from that — a wrong suggestion is free, a wrong takeover is not.

The concrete case that shaped the class table: ``cloud-debug`` ships as
``execution: mission`` with ``default_tier: monitor`` and NO triggers. It is
unreachable by matching today, and a fuzzy layer makes it reachable — so a
paraphrase could start an unrequested background worker in a git worktree.
"""
from __future__ import annotations

import pytest

from jarvis.skills import autofire_policy as policy
from jarvis.skills.guards import (
    VETO_AUTO_FIRE_NEVER,
    VETO_BAND_BELOW_FLOOR,
    VETO_DISPATCHING_CLASS,
    VETO_REASONS,
)
from jarvis.skills.match_eval import BAND_FIRE, BAND_NARROW, BAND_NONE


class _FakeRiskPolicy:
    def __init__(self, tier: str = "monitor") -> None:
        self.default_tier = tier


class _FakeFrontmatter:
    def __init__(
        self,
        *,
        tier: str = "monitor",
        execution: str = "inline",
        requires_tools: list[str] | None = None,
        plugin_id: str | None = None,
        auto_fire: str = "auto",
    ) -> None:
        self.risk_policy = _FakeRiskPolicy(tier)
        self.execution = execution
        self.requires_tools = requires_tools or []
        self.plugin_id = plugin_id
        self.auto_fire = auto_fire


class _FakeSkill:
    def __init__(
        self,
        name: str = "demo",
        frontmatter: _FakeFrontmatter | None = None,
        body: str = "# Demo\n\nDo the thing.\n",
    ) -> None:
        self.name = name
        self.frontmatter = frontmatter if frontmatter is not None else _FakeFrontmatter()
        self.body = body


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_a_plain_inline_skill_is_instruction_class() -> None:
    assert policy.classify(_FakeSkill()) == policy.CLASS_INSTRUCTION


@pytest.mark.parametrize("tier", ["safe", "monitor"])
def test_safe_and_monitor_tiers_stay_instruction(tier: str) -> None:
    skill = _FakeSkill(frontmatter=_FakeFrontmatter(tier=tier))
    assert policy.classify(skill) == policy.CLASS_INSTRUCTION


def test_requiring_a_tool_makes_it_tool_backed() -> None:
    skill = _FakeSkill(frontmatter=_FakeFrontmatter(requires_tools=["cli_gcloud"]))
    assert policy.classify(skill) == policy.CLASS_TOOL_BACKED


def test_a_paired_plugin_skill_is_tool_backed() -> None:
    skill = _FakeSkill(frontmatter=_FakeFrontmatter(plugin_id="gmail"))
    assert policy.classify(skill) == policy.CLASS_TOOL_BACKED


def test_ask_tier_is_tool_backed_not_instruction() -> None:
    """`ask` needs the voice-confirm machinery that lives in the orchestrator."""
    skill = _FakeSkill(frontmatter=_FakeFrontmatter(tier="ask"))
    assert policy.classify(skill) == policy.CLASS_TOOL_BACKED


def test_a_mission_skill_is_dispatching() -> None:
    """The cloud-debug shape: mission + monitor tier + no triggers."""
    skill = _FakeSkill(
        "cloud-debug", _FakeFrontmatter(execution="mission", tier="monitor")
    )
    assert policy.classify(skill) == policy.CLASS_DISPATCHING


def test_block_tier_is_dispatching() -> None:
    skill = _FakeSkill(frontmatter=_FakeFrontmatter(tier="block"))
    assert policy.classify(skill) == policy.CLASS_DISPATCHING


def test_a_legacy_macro_body_is_dispatching() -> None:
    """A body written for the retired macro executor may name arbitrary tools."""
    skill = _FakeSkill(body="# Old\n\nTOOL: run-shell {\"cmd\": \"rm -rf /\"}\n")
    assert policy.classify(skill) == policy.CLASS_DISPATCHING


def test_missing_frontmatter_is_dispatching() -> None:
    """Unknown intent is not granted capture rights."""
    skill = _FakeSkill()
    skill.frontmatter = None
    assert policy.classify(skill) == policy.CLASS_DISPATCHING


def test_an_unrecognised_tier_fails_toward_the_safer_class() -> None:
    skill = _FakeSkill(frontmatter=_FakeFrontmatter(tier="weird-new-tier"))
    assert policy.classify(skill) == policy.CLASS_TOOL_BACKED


# ---------------------------------------------------------------------------
# The tri-state override
# ---------------------------------------------------------------------------


def test_the_default_is_auto_not_opt_in() -> None:
    """A pure opt-in default would be the AP-27 shape.

    None of the maintainer's installed skills carry the field, so shipping
    opt-in would leave the answer to "has a skill ever fired?" at *no* while
    the release notes claimed otherwise.
    """
    assert policy.auto_fire_mode(_FakeSkill()) == policy.AUTO_FIRE_AUTO
    allowed, veto = policy.may_capture(_FakeSkill(), BAND_FIRE)
    assert allowed is True
    assert veto == ""


def test_never_opts_out_of_the_relevance_layer() -> None:
    skill = _FakeSkill(frontmatter=_FakeFrontmatter(auto_fire="never"))
    allowed, veto = policy.may_capture(skill, BAND_FIRE)
    assert allowed is False
    assert veto == VETO_AUTO_FIRE_NEVER


def test_a_user_override_beats_frontmatter() -> None:
    """Builtins need an admin password to edit and are re-copied by bootstrap,
    so the user's choice cannot live in their frontmatter."""
    skill = _FakeSkill(frontmatter=_FakeFrontmatter(auto_fire="auto"))
    assert policy.auto_fire_mode(skill, override="never") == policy.AUTO_FIRE_NEVER
    allowed, veto = policy.may_capture(skill, BAND_FIRE, override="never")
    assert allowed is False
    assert veto == VETO_AUTO_FIRE_NEVER


def test_an_invalid_override_falls_back_to_the_derived_default() -> None:
    assert policy.auto_fire_mode(_FakeSkill(), override="yolo") == policy.AUTO_FIRE_AUTO


# ---------------------------------------------------------------------------
# Capture permission
# ---------------------------------------------------------------------------


def test_narrow_does_not_capture_by_default() -> None:
    allowed, veto = policy.may_capture(_FakeSkill(), BAND_NARROW)
    assert allowed is False
    assert veto == VETO_BAND_BELOW_FLOOR


def test_narrow_captures_when_the_deployment_opens_it_up() -> None:
    allowed, veto = policy.may_capture(_FakeSkill(), BAND_NARROW, min_band="narrow")
    assert allowed is True
    assert veto == ""


def test_always_promotes_a_tool_backed_skill_into_the_weaker_band() -> None:
    skill = _FakeSkill(
        frontmatter=_FakeFrontmatter(requires_tools=["gmail"], auto_fire="always")
    )
    allowed, _ = policy.may_capture(skill, BAND_NARROW)
    assert allowed is True


def test_always_can_never_promote_a_dispatching_skill() -> None:
    """The one line no configuration may cross.

    No frontmatter field may authorize a deterministic matcher to start a
    background process — so `always` is checked AFTER the class veto, not before.
    """
    skill = _FakeSkill(
        "cloud-debug",
        _FakeFrontmatter(execution="mission", auto_fire="always"),
    )
    for band in (BAND_FIRE, BAND_NARROW):
        allowed, veto = policy.may_capture(skill, band, min_band="narrow")
        assert allowed is False
        assert veto == VETO_DISPATCHING_CLASS


def test_a_dispatching_skill_never_captures_even_at_fire() -> None:
    skill = _FakeSkill("cloud-debug", _FakeFrontmatter(execution="mission"))
    allowed, veto = policy.may_capture(skill, BAND_FIRE)
    assert allowed is False
    assert veto == VETO_DISPATCHING_CLASS


def test_band_none_never_captures() -> None:
    allowed, veto = policy.may_capture(_FakeSkill(), BAND_NONE)
    assert allowed is False
    assert veto == VETO_BAND_BELOW_FLOOR


def test_every_veto_is_in_the_closed_vocabulary() -> None:
    cases = (
        policy.may_capture(_FakeSkill(), BAND_NONE),
        policy.may_capture(_FakeSkill(), BAND_NARROW),
        policy.may_capture(
            _FakeSkill(frontmatter=_FakeFrontmatter(auto_fire="never")), BAND_FIRE
        ),
        policy.may_capture(
            _FakeSkill("m", _FakeFrontmatter(execution="mission")), BAND_FIRE
        ),
    )
    for allowed, veto in cases:
        assert allowed is False
        assert veto in VETO_REASONS


# ---------------------------------------------------------------------------
# Stand-downs
# ---------------------------------------------------------------------------


def test_only_instruction_at_fire_gets_the_full_stand_down() -> None:
    assert policy.stand_downs_allowed(_FakeSkill(), BAND_FIRE) is True


def test_a_tool_backed_skill_never_gets_the_full_stand_down() -> None:
    """Live bug 2026-06-21: plugin-discord suppressed the local-action fast path
    and a tool-less deep brain then claimed it had no Discord access."""
    skill = _FakeSkill("plugin-discord", _FakeFrontmatter(plugin_id="discord"))
    assert policy.stand_downs_allowed(skill, BAND_FIRE) is False


def test_narrow_never_gets_the_full_stand_down() -> None:
    assert policy.stand_downs_allowed(_FakeSkill(), BAND_NARROW) is False


def test_class_vocabulary_is_closed() -> None:
    for skill in (
        _FakeSkill(),
        _FakeSkill(frontmatter=_FakeFrontmatter(requires_tools=["x"])),
        _FakeSkill(frontmatter=_FakeFrontmatter(execution="mission")),
    ):
        assert policy.classify(skill) in policy.AUTOFIRE_CLASSES


def test_the_real_cloud_debug_skill_can_never_auto_fire() -> None:
    """End-to-end against the shape as it actually ships."""
    from jarvis.skills.schema import SkillFrontmatter

    frontmatter = SkillFrontmatter(
        name="cloud-debug",
        version="1.0.0",
        description="Dispatch a bug hunt to the background worker.",
        execution="mission",
    )

    class _Real:
        pass

    skill = _Real()
    skill.name = "cloud-debug"  # type: ignore[attr-defined]
    skill.frontmatter = frontmatter  # type: ignore[attr-defined]
    skill.body = "# Cloud Debug\n\nFind the root cause.\n"  # type: ignore[attr-defined]

    assert policy.classify(skill) == policy.CLASS_DISPATCHING
    allowed, veto = policy.may_capture(skill, BAND_FIRE, min_band="narrow")
    assert allowed is False
    assert veto == VETO_DISPATCHING_CLASS
