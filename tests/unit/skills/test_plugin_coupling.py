from pathlib import Path

from jarvis.skills.plugin_coupling import PAIRED_CAP_PREFIX, capability_from_skill
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState


def _skill(**fm) -> Skill:
    front = SkillFrontmatter(**fm)
    return Skill(
        path=Path("x/SKILL.md"), frontmatter=front, body="guidance",
        state=SkillLifecycleState.ACTIVE,
    )


def test_capability_from_paired_skill():
    sk = _skill(
        name="plugin-gmail", plugin_id="gmail",
        description="Read and send mail from the connected Gmail inbox.",
        intent_verbs=["lies", "schick", "antworte"],
        intent_objects=["postfach", "inbox", "gmail"],
        risk_policy={"default_tier": "ask"},
    )
    cap = capability_from_skill(sk)
    assert cap is not None
    assert cap.id == f"{PAIRED_CAP_PREFIX}gmail"
    assert cap.source == "skill"
    assert "lies" in cap.verbs and "postfach" in cap.objects
    assert cap.risk_tier == "ask"


def test_no_capability_without_intent_vocab():
    sk = _skill(name="plugin-empty", plugin_id="empty")
    assert capability_from_skill(sk) is None


def test_standalone_skill_with_intent_gets_capability():
    sk = _skill(
        name="morning-routine", description="Run the morning routine.",
        intent_verbs=["starte"], intent_objects=["morgenroutine", "routine"],
    )
    cap = capability_from_skill(sk)
    assert cap is not None
    assert cap.id == f"{PAIRED_CAP_PREFIX}morning-routine"


def test_draft_skill_yields_no_capability():
    sk = _skill(name="plugin-gmail", plugin_id="gmail",
                intent_verbs=["lies"], intent_objects=["postfach"])
    object.__setattr__(sk, "state", SkillLifecycleState.DRAFT)
    assert capability_from_skill(sk) is None


def test_gmail_objects_do_not_match_email_validation():
    """HARD NEGATIVE: a curated Gmail skill must NOT resolve a coding task."""
    from jarvis.core.capabilities import CapabilityRegistry
    sk = _skill(
        name="plugin-gmail", plugin_id="gmail",
        description="Gmail inbox.",
        intent_verbs=["lies", "schick", "antworte", "zeig"],
        intent_objects=["postfach", "inbox", "gmail", "mails", "nachrichten"],
        risk_policy={"default_tier": "ask"},
    )
    reg = CapabilityRegistry()
    reg.register(capability_from_skill(sk))
    assert reg.resolve_intent("implementier eine Email-Validation") is None  # i18n-allow
    assert reg.resolve_intent("lies meine letzte Mail aus dem Postfach") is not None  # i18n-allow
