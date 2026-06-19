"""The Tools panel must reflect ActionExecuted outcomes, not assume success.

Regression found in the Chrome checkup: failed Computer-Use actions (open_app,
click_element) were rendered as "ok" because merge_action_tools only read
ActionProposed/Approved (risk + approval) and never the execution result, so
ToolCall.success stayed at its default True."""
from jarvis.runs.analyzer import merge_action_tools
from jarvis.sessions.models import VoiceEventRow


def _ev(kind, **payload):
    return VoiceEventRow(session_id="s", turn_id="t", ts_ms=0, kind=kind, payload=payload)


def test_failed_execution_marks_tool_failed_with_error():
    events = [
        _ev("ActionProposed", tool_name="open_app", risk_tier="safe"),
        _ev("ActionExecuted", tool_name="open_app", success=False,
            error="Anwendung 'settings' nicht gefunden"),
    ]
    tools = merge_action_tools(events, [])
    t = next(x for x in tools if x.name == "open_app")
    assert t.success is False
    assert t.error_line and "settings" in t.error_line


def test_successful_execution_keeps_tool_ok():
    events = [
        _ev("ActionProposed", tool_name="hotkey", risk_tier="monitor"),
        _ev("ActionExecuted", tool_name="hotkey", success=True),
    ]
    tools = merge_action_tools(events, [])
    assert next(x for x in tools if x.name == "hotkey").success is True


def test_any_failure_across_repeats_marks_failed():
    # click_element runs twice (one ok, one failed) -> the summary row is failed.
    events = [
        _ev("ActionProposed", tool_name="click_element", risk_tier="monitor"),
        _ev("ActionExecuted", tool_name="click_element", success=True),
        _ev("ActionExecuted", tool_name="click_element", success=False, error="No matching element"),
    ]
    tools = merge_action_tools(events, [])
    t = next(x for x in tools if x.name == "click_element")
    assert t.success is False
    assert "No matching element" in (t.error_line or "")


def test_executed_only_tool_is_added():
    # A tool that executed without a separate ActionProposed still appears.
    events = [_ev("ActionExecuted", tool_name="computer_use", success=True)]
    tools = merge_action_tools(events, [])
    assert any(x.name == "computer_use" for x in tools)
