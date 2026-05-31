"""Unit-Tests für TriggerMatcher + Hotkey-Normalization."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.loader import parse_skill
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.trigger_matcher import TriggerMatcher, normalize_hotkey


def _write_skill(root: Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


VOICE_DE = """---
schema_version: "1"
name: voice_de
triggers:
  - type: voice
    pattern: "starte (das )?meeting"
    language: ["de"]
---
body
"""

VOICE_EN = """---
schema_version: "1"
name: voice_en
triggers:
  - type: voice
    pattern: "start (the )?meeting"
    language: ["en"]
---
body
"""

HOTKEY_SKILL = """---
schema_version: "1"
name: hotkey_skill
triggers:
  - type: hotkey
    combo: "ctrl+right_alt+j"
---
body
"""

CRON_SKILL = """---
schema_version: "1"
name: cron_skill
triggers:
  - type: schedule
    cron: "0 9 * * *"
---
body
"""


@pytest.fixture
def registry(tmp_path: Path) -> SkillRegistry:
    _write_skill(tmp_path, "voice_de", VOICE_DE)
    _write_skill(tmp_path, "voice_en", VOICE_EN)
    _write_skill(tmp_path, "hotkey_skill", HOTKEY_SKILL)
    _write_skill(tmp_path, "cron_skill", CRON_SKILL)
    reg = SkillRegistry(tmp_path)
    reg.reload_sync()
    return reg


def test_match_voice_de(registry: SkillRegistry):
    m = TriggerMatcher(registry)
    sk = m.match_voice("Bitte starte das meeting jetzt", lang="de")
    assert sk is not None
    assert sk.name == "voice_de"


def test_match_voice_en(registry: SkillRegistry):
    m = TriggerMatcher(registry)
    sk = m.match_voice("please start the meeting", lang="en")
    assert sk is not None
    assert sk.name == "voice_en"


def test_match_voice_no_match(registry: SkillRegistry):
    m = TriggerMatcher(registry)
    assert m.match_voice("komplett anderes zeug", lang="de") is None


def test_match_voice_auto_lang(registry: SkillRegistry):
    m = TriggerMatcher(registry)
    sk = m.match_voice("starte meeting", lang="auto")
    assert sk is not None


def test_match_hotkey_normalization(registry: SkillRegistry):
    m = TriggerMatcher(registry)
    for variant in [
        "ctrl+right_alt+j",
        "RIGHT_ALT+CTRL+J",
        "j+ctrl+right_alt",
    ]:
        sk = m.match_hotkey(variant)
        assert sk is not None, f"failed for: {variant}"
        assert sk.name == "hotkey_skill"


def test_match_hotkey_none_on_miss(registry: SkillRegistry):
    m = TriggerMatcher(registry)
    assert m.match_hotkey("ctrl+q") is None


def test_normalize_hotkey_basic():
    assert normalize_hotkey("Ctrl+Alt+J") == "alt+ctrl+j"
    assert normalize_hotkey("J+CTRL+ALT") == "alt+ctrl+j"
    assert normalize_hotkey("") == ""
    assert normalize_hotkey("j") == "j"


# ----------------------------------------------------------------------
# State-Filter-Tests (Skills-Brain-Integration: Phase Skills-1)
# ----------------------------------------------------------------------


import dataclasses

from jarvis.skills.schema import SkillLifecycleState


def _force_state(reg: SkillRegistry, skill_name: str, state: SkillLifecycleState) -> None:
    """Ersetzt einen Skill im Registry-Dict mit veraendertem state.

    Skill ist frozen — dataclasses.replace ist die saubere Methode.
    """
    sk = reg._skills[skill_name]
    reg._skills[skill_name] = dataclasses.replace(sk, state=state)


def test_draft_skill_not_matched_voice(registry: SkillRegistry):
    """DRAFT-Skills duerfen nicht via Voice-Trigger feuern."""
    _force_state(registry, "voice_de", SkillLifecycleState.DRAFT)
    m = TriggerMatcher(registry)
    assert m.match_voice("starte das meeting", lang="de") is None


def test_disabled_skill_not_matched_voice(registry: SkillRegistry):
    """DISABLED-Skills duerfen nicht via Voice-Trigger feuern."""
    _force_state(registry, "voice_en", SkillLifecycleState.DISABLED)
    m = TriggerMatcher(registry)
    assert m.match_voice("start the meeting", lang="en") is None


def test_draft_skill_not_matched_hotkey(registry: SkillRegistry):
    """DRAFT-Skills duerfen nicht via Hotkey-Trigger feuern."""
    _force_state(registry, "hotkey_skill", SkillLifecycleState.DRAFT)
    m = TriggerMatcher(registry)
    assert m.match_hotkey("ctrl+right_alt+j") is None


def test_validated_skill_matches(registry: SkillRegistry):
    """VALIDATED-Skills (vor Activation) duerfen weiter matchen."""
    _force_state(registry, "voice_de", SkillLifecycleState.VALIDATED)
    m = TriggerMatcher(registry)
    sk = m.match_voice("starte das meeting", lang="de")
    assert sk is not None
    assert sk.name == "voice_de"


def test_resolve_priority_hotkey_over_voice(registry: SkillRegistry):
    m = TriggerMatcher(registry)
    sk = m.resolve(hotkey="ctrl+right_alt+j", utterance="starte meeting", lang="de")
    assert sk is not None
    assert sk.name == "hotkey_skill"


def test_by_trigger_separation(registry: SkillRegistry):
    voice = registry.by_trigger("voice")
    hotkey = registry.by_trigger("hotkey")
    schedule = registry.by_trigger("schedule")
    assert {s.name for s in voice} == {"voice_de", "voice_en"}
    assert {s.name for s in hotkey} == {"hotkey_skill"}
    assert {s.name for s in schedule} == {"cron_skill"}
