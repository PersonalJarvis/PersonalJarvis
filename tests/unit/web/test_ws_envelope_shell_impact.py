"""The WS envelope enriches run_shell ActionProposed with the command impact.

The live command activity UI shows a plain-language badge (reads / modifies /
deletes) next to a running command. The classification happens once, at the UI
boundary, with the SAME ``jarvis.safety.command_impact`` module the risk-tier
hook and the voice confirmation use — no TypeScript twin to drift.
"""
from __future__ import annotations

from uuid import uuid4

from jarvis.core.events import ActionExecuted, ActionProposed
from jarvis.ui.web.schema import event_to_ws_envelope


def _proposed(tool_name: str, args: dict) -> ActionProposed:
    return ActionProposed(
        trace_id=uuid4(), tool_name=tool_name, args=args, risk_tier="monitor",
    )


def test_run_shell_proposed_carries_the_impact() -> None:
    envelope = event_to_ws_envelope(
        _proposed("run_shell", {"command": "rm -rf build"})
    )
    assert envelope["payload"]["impact"] == {
        "level": "destructive",
        "commands": "rm",
    }


def test_read_command_classifies_as_read() -> None:
    envelope = event_to_ws_envelope(
        _proposed("run_shell", {"command": "ls | grep foo"})
    )
    assert envelope["payload"]["impact"]["level"] == "read"
    assert "ls" in envelope["payload"]["impact"]["commands"]


def test_other_tools_are_not_enriched() -> None:
    envelope = event_to_ws_envelope(
        _proposed("open_app", {"app": "notepad"})
    )
    assert "impact" not in envelope["payload"]


def test_missing_command_is_not_enriched() -> None:
    envelope = event_to_ws_envelope(_proposed("run_shell", {}))
    assert "impact" not in envelope["payload"]


def test_action_executed_is_untouched() -> None:
    envelope = event_to_ws_envelope(
        ActionExecuted(
            trace_id=uuid4(), tool_name="run_shell", success=True, duration_ms=12,
        )
    )
    assert "impact" not in envelope["payload"]
