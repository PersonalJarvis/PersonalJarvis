"""A skill-matched turn injects the skill's instructions into the turn context
deterministically (AD-S4: "no run-skill round trip needed"). But the run-skill
tool stayed visible, so a weak model (gemini-3.5-flash) made a REDUNDANT
run-skill call and leaked the raw ``<call:tool.run-skill ...>`` text instead of
executing it (forensic 2026-06-24).

Hiding run-skill once the instructions are already inline makes skill execution
PROVIDER- and MODEL-agnostic: no tool call is needed by any model (gemini-fast,
Claude, or a tool-incapable CLI brain) — it just follows the injected
instructions. These tests pin that gate.

2026-07-25, deterministic relevance layer: dropping run-skill also removes the
model's only way to signal "wrong skill". That is acceptable when a human wrote
the trigger phrase, and NOT acceptable when a scorer merely inferred the match
from a paraphrase — so the gate became conditional on the turn being entitled to
the full stand-down. Both sides are covered below, because the asymmetry IS the
safety property.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.skills.match_eval import (
    BAND_FIRE,
    BAND_NARROW,
    SOURCE_RELEVANCE,
    SOURCE_TRIGGER,
    MatchCandidate,
    MatchDecision,
)


def _mgr() -> BrainManager:
    return BrainManager.__new__(BrainManager)  # bypass heavy __init__


class _Policy:
    def __init__(self, tier: str = "monitor") -> None:
        self.default_tier = tier


class _Frontmatter:
    def __init__(
        self,
        *,
        tier: str = "monitor",
        requires_tools: list[str] | None = None,
        plugin_id: str | None = None,
        execution: str = "inline",
    ) -> None:
        self.risk_policy = _Policy(tier)
        self.requires_tools = requires_tools or []
        self.plugin_id = plugin_id
        self.execution = execution
        self.auto_fire = "auto"


class _Skill:
    def __init__(self, name: str = "demo", frontmatter: _Frontmatter | None = None) -> None:
        self.name = name
        self.frontmatter = frontmatter or _Frontmatter()
        self.body = "Do the thing."


def _decision(source: str, band: str) -> MatchDecision:
    candidate = MatchCandidate(skill_name="demo", score=1.5, band=band, source=source)
    return MatchDecision(band=band, source=source, top=candidate, candidates=(candidate,))


# ---------------------------------------------------------------------------
# The historical case: an author's trigger keeps its unconditional rights
# ---------------------------------------------------------------------------


def test_run_skill_hidden_when_injected_inline() -> None:
    """A trigger-matched turn still drops run-skill (unchanged behaviour)."""
    m = _mgr()
    m._skill_injected_inline = True
    m._skill_turn_match = _Skill()
    m._skill_relevance = None  # no relevance decision == the trigger path
    tools = {"run-skill": object(), "screenshot": object()}
    out = m._drop_run_skill_when_inline_injected(tools)
    assert "run-skill" not in out
    assert "screenshot" in out


def test_run_skill_hidden_when_the_decision_reports_the_trigger_channel() -> None:
    m = _mgr()
    m._skill_injected_inline = True
    m._skill_turn_match = _Skill()
    m._skill_relevance = _decision(SOURCE_TRIGGER, BAND_FIRE)
    out = m._drop_run_skill_when_inline_injected({"run-skill": object()})
    assert "run-skill" not in out


def test_run_skill_hidden_for_an_instruction_skill_at_fire() -> None:
    """An instruction-only skill has a one-turn blast radius, so it may capture
    fully even when a scorer inferred it."""
    m = _mgr()
    m._skill_injected_inline = True
    m._skill_turn_match = _Skill(frontmatter=_Frontmatter(tier="safe"))
    m._skill_relevance = _decision(SOURCE_RELEVANCE, BAND_FIRE)
    out = m._drop_run_skill_when_inline_injected({"run-skill": object()})
    assert "run-skill" not in out


# ---------------------------------------------------------------------------
# The new case: an inferred match must leave the model an escape hatch
# ---------------------------------------------------------------------------


def test_run_skill_kept_for_an_inferred_tool_backed_skill() -> None:
    """The core safety property of the relevance layer.

    Nobody stated an intent for this skill — a scorer inferred it from a
    paraphrase. Dropping run-skill would take away the model's only way to say
    "wrong skill", exactly where that ability matters most. Live precedent:
    plugin-discord keyword-matched the bare word "Discord", suppressed the
    deterministic desktop path, and a tool-less deep brain then claimed it had
    no Discord access (2026-06-21).
    """
    m = _mgr()
    m._skill_injected_inline = True
    m._skill_turn_match = _Skill(
        frontmatter=_Frontmatter(requires_tools=["gmail"], plugin_id="gmail")
    )
    m._skill_relevance = _decision(SOURCE_RELEVANCE, BAND_FIRE)
    out = m._drop_run_skill_when_inline_injected({"run-skill": object()})
    assert "run-skill" in out, "an inferred tool-backed match must stay declinable"


def test_run_skill_kept_for_an_inferred_match_below_fire() -> None:
    m = _mgr()
    m._skill_injected_inline = True
    m._skill_turn_match = _Skill(frontmatter=_Frontmatter(tier="safe"))
    m._skill_relevance = _decision(SOURCE_RELEVANCE, BAND_NARROW)
    out = m._drop_run_skill_when_inline_injected({"run-skill": object()})
    assert "run-skill" in out


def test_run_skill_kept_for_an_ask_tier_skill() -> None:
    """`ask` needs the confirmation machinery, so it is never instruction-class."""
    m = _mgr()
    m._skill_injected_inline = True
    m._skill_turn_match = _Skill(frontmatter=_Frontmatter(tier="ask"))
    m._skill_relevance = _decision(SOURCE_RELEVANCE, BAND_FIRE)
    out = m._drop_run_skill_when_inline_injected({"run-skill": object()})
    assert "run-skill" in out


# ---------------------------------------------------------------------------
# Unchanged edges
# ---------------------------------------------------------------------------


def test_run_skill_kept_when_not_injected() -> None:
    m = _mgr()
    m._skill_injected_inline = False
    tools = {"run-skill": object(), "screenshot": object()}
    out = m._drop_run_skill_when_inline_injected(tools)
    assert "run-skill" in out
    assert "screenshot" in out


def test_gate_is_noop_on_non_dict() -> None:
    """A router-lead turn can pass a non-dict tool set through; the gate must
    not choke on it."""
    m = _mgr()
    m._skill_injected_inline = True
    m._skill_turn_match = _Skill()
    m._skill_relevance = None
    assert m._drop_run_skill_when_inline_injected(None) is None
