"""E2E: a paired Gmail skill makes inbox requests reachable, not refused.

Uses an ISOLATED CapabilityRegistry (never the global singleton) so it cannot
pollute the seed-only fixture in test_capability_coupling_e2e.py.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.brain.local_action_gate import LocalActionMode, match_local_action
from jarvis.core.capabilities import CapabilityRegistry
from jarvis.core.capabilities_seed import seed_registry
from jarvis.skills.plugin_coupling import register_paired_capabilities
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState


def _registry_with_gmail() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    seed_registry(reg)
    gmail = Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail",
            plugin_id="gmail",
            description="Read and send mail from the connected Gmail inbox.",
            intent_verbs=[  # i18n-allow
                "lies", "lese", "schick", "sende", "antworte", "zeig", "check",  # i18n-allow
            ],
            intent_objects=[  # i18n-allow
                "postfach", "inbox", "gmail", "mail",  # i18n-allow
                "email", "mails", "nachrichten", "posteingang",  # i18n-allow
            ],
            risk_policy={"default_tier": "ask"},
        ),
        body="Use gmail/* tools to read and send mail.",
        state=SkillLifecycleState.ACTIVE,
    )
    register_paired_capabilities(reg, [gmail])
    return reg


def test_gmail_requests_are_reachable_not_refused():
    """With Gmail paired, the gate must NOT return UNSUPPORTED for real inbox
    requests -- they resolve and fall through to the tool-use loop."""
    reg = _registry_with_gmail()
    for utt in [
        "Schick eine Email an harald@gmx.de mit dem Betreff Hallo",  # i18n-allow
        "lies meine letzte Mail aus dem Postfach",  # i18n-allow
        "check mein Postfach",  # i18n-allow
    ]:
        plan = match_local_action(utt, lang="de", _registry=reg)
        assert plan is None or plan.mode is not LocalActionMode.UNSUPPORTED, (
            f"{utt!r} was refused (UNSUPPORTED) despite Gmail being paired"
        )


def test_other_dispatch_domains_stay_unsupported_when_gmail_paired():
    """Pairing Gmail must NOT make a different domain's dispatch reachable."""
    reg = _registry_with_gmail()
    for utt in [
        "Sende eine WhatsApp an Mama",  # i18n-allow
        "Bestelle eine Pizza",  # i18n-allow
    ]:
        plan = match_local_action(utt, lang="de", _registry=reg)
        assert plan is not None and plan.mode is LocalActionMode.UNSUPPORTED, (
            f"{utt!r} should stay UNSUPPORTED -- gmail must not capture it"
        )


def test_email_validation_coding_task_stays_non_gmail():
    """A coding task that merely names email must not resolve to gmail."""
    reg = _registry_with_gmail()
    cap = reg.resolve_intent("implementier eine Email-Validation")  # i18n-allow
    assert cap is None or cap.id != "skill.paired.gmail", f"-> {cap}"
