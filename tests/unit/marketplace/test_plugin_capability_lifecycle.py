from pathlib import Path

from jarvis.core.capabilities import CapabilityRegistry
from jarvis.marketplace.plugin_registry import (
    _deregister_plugin_capability,
    _register_plugin_capability,
)
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState


def _gmail_skill() -> Skill:
    return Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail", plugin_id="gmail", description="Gmail.",
            intent_verbs=["lies", "schick"],  # i18n-allow
            intent_objects=["postfach", "inbox"],  # i18n-allow
            risk_policy={"default_tier": "ask"},
        ),
        body="g", state=SkillLifecycleState.ACTIVE,
    )


def test_connect_registers_and_disconnect_removes():
    reg = CapabilityRegistry()
    _register_plugin_capability(reg, "gmail", [_gmail_skill()])
    assert reg.resolve_intent("lies mein postfach") is not None  # i18n-allow
    _deregister_plugin_capability(reg, "gmail")
    assert reg.resolve_intent("lies mein postfach") is None  # i18n-allow


def test_register_ignores_unrelated_plugin():
    reg = CapabilityRegistry()
    # a skill paired to a DIFFERENT plugin must not register under "gmail"
    _register_plugin_capability(reg, "stripe", [_gmail_skill()])
    assert reg.resolve_intent("lies mein postfach") is None  # i18n-allow
