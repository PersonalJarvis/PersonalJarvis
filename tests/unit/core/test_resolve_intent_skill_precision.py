from pathlib import Path

from jarvis.core.capabilities import CapabilityRegistry
from jarvis.core.capabilities_seed import seed_registry
from jarvis.skills.plugin_coupling import register_paired_capabilities
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState


def _reg_with_gmail() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    seed_registry(reg)
    gmail = Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail", plugin_id="gmail", description="Gmail.",
            intent_verbs=[  # i18n-allow
                "lies", "lese", "schick", "sende", "antworte", "zeig", "check",
            ],
            intent_objects=[  # i18n-allow
                "postfach", "inbox", "gmail", "mail", "email",
                "mails", "nachrichten", "posteingang",
            ],
            risk_policy={"default_tier": "ask"},
        ),
        body="g", state=SkillLifecycleState.ACTIVE,
    )
    register_paired_capabilities(reg, [gmail])
    return reg


def test_gmail_request_with_domain_noun_resolves_to_gmail():
    reg = _reg_with_gmail()
    for utt in [
        "Schick eine Email an harald@example.com mit dem Betreff Hallo",  # i18n-allow
        "lies meine letzte Mail aus dem Postfach",  # i18n-allow
        "check mein Postfach",  # i18n-allow
    ]:
        cap = reg.resolve_intent(utt)
        assert cap is not None and cap.id == "skill.paired.gmail", f"{utt!r} -> {cap}"


def test_generic_verb_without_gmail_noun_does_not_hijack():
    reg = _reg_with_gmail()
    for utt in [
        "Sende eine WhatsApp an Mama",  # i18n-allow
        "Bestelle eine Pizza",  # i18n-allow
        "Poste auf X dass ich heute frei habe",  # i18n-allow
        "Trag einen Termin morgen 10 Uhr ein",  # i18n-allow
    ]:
        cap = reg.resolve_intent(utt)
        assert cap is None or cap.id != "skill.paired.gmail", f"{utt!r} -> {cap}"


def test_seed_harness_caps_unchanged_by_skill_rules():
    reg = _reg_with_gmail()
    cap = reg.resolve_intent("Lies die Datei foo.txt")  # i18n-allow
    assert cap is not None and cap.source == "harness"
