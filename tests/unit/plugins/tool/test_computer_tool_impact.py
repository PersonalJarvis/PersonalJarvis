"""The ``computer`` tool speaks the impact contract every caller reads.

Its ``describe_args`` returned a plain string, so the voice read check failed
on every call (logged as a warning) and a screenshot-only call was keyed for
replay: a repeated look at the screen could be answered with an earlier one.
"""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.plugins.tool.computer import ComputerTool
from jarvis.realtime.tools import _call_only_reads


def _steps(*actions: str) -> dict:
    return {"steps": [{"action": action} for action in actions]}


def test_looking_at_the_screen_only_reads() -> None:
    tool = ComputerTool()
    for args in (_steps("screenshot"), _steps("wait", "screenshot"), {"action": "screenshot"}):
        impact = tool.describe_args(args)
        assert impact["level"] == "read", args
        assert "screenshot" in impact["commands"] or "wait" in impact["commands"]


def test_any_input_step_is_a_change() -> None:
    tool = ComputerTool()
    impact = tool.describe_args(_steps("screenshot", "click"))
    assert impact == {"level": "modify", "commands": "Operate the screen: screenshot, click"}


def test_a_bare_input_step_or_a_malformed_call_is_never_a_read() -> None:
    """Fail closed: ``parse_steps`` runs a bare step, so its action decides."""
    tool = ComputerTool()
    for args in (
        {"action": "click", "x": 10, "y": 20}, {}, {"steps": []}, {"steps": "screenshot"},
        {"steps": [{"action": "screenshot"}, "click"]}, {"steps": [{"action": "SCREENSHOT "}, {}]},
    ):
        assert tool.describe_args(args)["level"] == "modify", args


def test_the_read_check_understands_it() -> None:
    descriptor = SimpleNamespace(describe_args=ComputerTool().describe_args)
    assert _call_only_reads(descriptor, _steps("screenshot")) is True
    assert _call_only_reads(descriptor, _steps("type_text")) is False
