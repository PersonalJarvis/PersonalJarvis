"""P2 fix: a smalltalk turn hides spawn/action tools (anti-fake-spawn, 2026-05-01)
but KEEPS the read-only screenshot tool, so the brain can still look at the screen
on demand (Wave 2) even when the turn was classified smalltalk — e.g. the live
2026-05-31 failure "Hallo, lies mir vor was oben links steht" left the brain with
no screenshot tool and it could only stall ("ich schaue nach … einen Moment")."""  # i18n-allow: verbatim quote of the hallucinated runtime output
from __future__ import annotations

from jarvis.brain.manager import BrainManager


def _mgr(tools: dict) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._tools = tools
    return m


def test_smalltalk_keeps_screenshot_hides_spawn() -> None:
    m = _mgr({"screenshot": object(), "spawn_worker": object(), "run_shell": object()})
    override = m._smalltalk_tool_override()
    assert set(override) == {"screenshot"}


def test_smalltalk_override_empty_without_screenshot() -> None:
    m = _mgr({"spawn_worker": object(), "run_shell": object()})
    assert m._smalltalk_tool_override() == {}
