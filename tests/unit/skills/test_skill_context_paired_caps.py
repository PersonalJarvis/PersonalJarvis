"""Tests: set_skill_context registers paired-skill capabilities at context set time.

Task 4.6 — real-boot fix: the brain is built before the skill context is set
(desktop_app.py builds the brain at ~line 616, sets the context inside
_start_speech_and_orb at ~line 1509). The boot-seed therefore sees no context
and no paired capabilities land. set_skill_context is the single timing-robust
registration point.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.core.capabilities import get_registry
from jarvis.skills.plugin_coupling import PAIRED_CAP_PREFIX
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState
from jarvis.skills.skill_context import SkillContext, set_skill_context


class _FakeReg:
    def __init__(self, skills):
        self._skills = skills

    def list(self):
        return self._skills


def _gmail_skill() -> Skill:
    return Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail", plugin_id="gmail", description="Gmail.",
            intent_verbs=["lies", "check"],  # i18n-allow
            intent_objects=["postfach", "inbox"],  # i18n-allow
            risk_policy={"default_tier": "ask"},
        ),
        body="g", state=SkillLifecycleState.ACTIVE,
    )


def test_set_skill_context_registers_paired_capabilities():
    cap_id = f"{PAIRED_CAP_PREFIX}gmail"
    try:
        set_skill_context(SkillContext(registry=_FakeReg([_gmail_skill()]), runner=None))
        cap = get_registry().resolve_intent("check mein Postfach")  # i18n-allow
        assert cap is not None and cap.id == cap_id, f"-> {cap}"
    finally:
        get_registry().deregister(cap_id)
        set_skill_context(None)


def test_set_skill_context_none_is_safe():
    # Clearing the context must not raise and must not register anything.
    set_skill_context(None)  # must not raise
