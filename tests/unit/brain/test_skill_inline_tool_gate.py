"""A skill-matched turn injects the skill's instructions into the turn context
deterministically (AD-S4: "no run-skill round trip needed"). But the run-skill
tool stayed visible, so a weak model (gemini-3.5-flash) made a REDUNDANT
run-skill call and leaked the raw ``<call:tool.run-skill ...>`` text instead of
executing it (forensic 2026-06-24).

Hiding run-skill once the instructions are already inline makes skill execution
PROVIDER- and MODEL-agnostic: no tool call is needed by any model (gemini-fast,
Claude, or a tool-incapable CLI brain) — it just follows the injected
instructions. These tests pin that gate.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager


def _mgr() -> BrainManager:
    return BrainManager.__new__(BrainManager)  # bypass heavy __init__


def test_run_skill_hidden_when_injected_inline() -> None:
    m = _mgr()
    m._skill_injected_inline = True
    tools = {"run-skill": object(), "screenshot": object()}
    out = m._drop_run_skill_when_inline_injected(tools)
    assert "run-skill" not in out
    assert "screenshot" in out


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
    assert m._drop_run_skill_when_inline_injected(None) is None
