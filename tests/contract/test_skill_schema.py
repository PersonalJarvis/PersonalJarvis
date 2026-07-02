"""Contract tests for the Skill frontmatter schema (pydantic)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.skills.schema import (
    SkillFrontmatter,
    SkillLifecycleState,
    SkillRiskPolicy,
    SkillTrigger,
)


def test_valid_minimal_frontmatter():
    fm = SkillFrontmatter.model_validate({"name": "test_skill"})
    assert fm.name == "test_skill"
    assert fm.schema_version == "1"
    assert fm.token_budget_estimate == 2000
    assert fm.risk_policy.default_tier == "monitor"


def test_valid_full_frontmatter():
    fm = SkillFrontmatter.model_validate({
        "schema_version": "1",
        "name": "email_summary",
        "version": "1.2.0",
        "description": "Fasst Inbox zusammen",
        "category": "productivity",
        "tags": ["email", "gmail"],
        "author": "Harald",
        "license": "MIT",
        "triggers": [
            {"type": "voice", "pattern": r"fass.*(?:inbox|emails) zusammen",
             "language": ["de"]},
            {"type": "hotkey", "combo": "ctrl+right_alt+e"},
            {"type": "schedule", "cron": "0 9 * * *"},
        ],
        "requires_tools": ["gmail_list", "gmail_read"],
        "risk_policy": {
            "default_tier": "ask",
            "per_tool_overrides": {"gmail_list": "safe"},
            "require_confirmation": [],
        },
        "config": {"max_results": 20},
        "token_budget_estimate": 5000,
    })
    assert fm.name == "email_summary"
    assert len(fm.triggers) == 3
    assert fm.risk_policy.per_tool_overrides["gmail_list"] == "safe"


def test_missing_name_raises():
    with pytest.raises(ValidationError):
        SkillFrontmatter.model_validate({})


def test_empty_name_raises():
    with pytest.raises(ValidationError):
        SkillFrontmatter.model_validate({"name": "  "})


def test_invalid_schema_version_raises():
    with pytest.raises(ValidationError):
        SkillFrontmatter.model_validate({"name": "x", "schema_version": "2"})


def test_invalid_trigger_type_raises():
    with pytest.raises(ValidationError):
        SkillFrontmatter.model_validate({
            "name": "x",
            "triggers": [{"type": "telepathy"}],
        })


def test_token_budget_bounds():
    with pytest.raises(ValidationError):
        SkillFrontmatter.model_validate({
            "name": "x",
            "token_budget_estimate": 0,
        })


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        SkillFrontmatter.model_validate({"name": "x", "surprise": True})


def test_trigger_payload_validation():
    t = SkillTrigger(type="voice")
    assert "voice trigger needs 'pattern'" in t.validate_payload()

    t2 = SkillTrigger(type="hotkey", combo="ctrl+j")
    assert t2.validate_payload() == []

    t3 = SkillTrigger(type="schedule")
    assert "schedule trigger needs 'cron'" in t3.validate_payload()


def test_risk_policy_default():
    rp = SkillRiskPolicy()
    assert rp.default_tier == "monitor"
    assert rp.per_tool_overrides == {}


def test_lifecycle_state_is_str_enum():
    assert SkillLifecycleState.ACTIVE.value == "active"
    assert SkillLifecycleState("draft") is SkillLifecycleState.DRAFT
