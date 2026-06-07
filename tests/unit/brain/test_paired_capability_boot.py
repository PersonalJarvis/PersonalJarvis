from pathlib import Path

from jarvis.core.capabilities import CapabilityRegistry
from jarvis.skills.plugin_coupling import register_paired_capabilities
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState


def test_boot_registers_paired_gmail_capability():
    reg = CapabilityRegistry()
    gmail = Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail", plugin_id="gmail",
            description="Gmail inbox.",
            intent_verbs=["lies", "schick", "antworte", "zeig"],  # i18n-allow
            intent_objects=["postfach", "inbox", "gmail", "mails"],  # i18n-allow
            risk_policy={"default_tier": "ask"},
        ),
        body="g", state=SkillLifecycleState.ACTIVE,
    )
    n = register_paired_capabilities(reg, [gmail])
    assert n == 1
    resolved = reg.resolve_intent("schick eine Mail an Sam aus meinem Postfach")  # i18n-allow
    assert resolved is not None
