import pytest
from pydantic import ValidationError

from jarvis.skills.schema import SkillFrontmatter


def test_pairing_fields_default_empty():
    fm = SkillFrontmatter(name="x")
    assert fm.plugin_id is None
    assert fm.intent_verbs == []
    assert fm.intent_objects == []


def test_pairing_fields_roundtrip():
    fm = SkillFrontmatter(
        name="plugin-gmail",
        plugin_id="gmail",
        intent_verbs=["lies", "schick", "antworte"],
        intent_objects=["postfach", "inbox", "gmail"],
    )
    assert fm.plugin_id == "gmail"
    assert "postfach" in fm.intent_objects


def test_extra_forbid_rejects_typo():
    # extra="forbid" must still reject typos
    with pytest.raises(ValidationError):
        SkillFrontmatter(name="x", pluginid="typo")
