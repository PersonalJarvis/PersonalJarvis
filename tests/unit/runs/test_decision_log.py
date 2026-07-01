"""Session-Decision-Log: the analyzer surfaces the honest "why" + tool I/O.

The model rationale (ActionProposed.rationale) is captured for free and must be
attached to the route step; rule-based steps (approval/denial/tier/brain/
fallback) get a deterministic, human-readable explanation built from CAPTURED
facts — never fabricated. The already-captured tool command/output is surfaced."""
from jarvis.runs.analyzer import attach_tool_io, build_decision_path
from jarvis.runs.model import ToolCall
from jarvis.sessions.models import VoiceEventRow


def _ev(kind, ts_ms=0, **payload):
    return VoiceEventRow(session_id="s", turn_id="t1", ts_ms=ts_ms, kind=kind, payload=payload)


def _step(steps, kind):
    return next(s for s in steps if s.kind == kind)


def test_proposed_attaches_model_rationale():
    events = [
        _ev("ActionProposed", ts_ms=2, tool_name="open_calendar", risk_tier="safe",
            rationale="I open the calendar to check your schedule."),
    ]
    route = _step(build_decision_path(events), "route")
    assert route.rationale.startswith("I open the calendar")
    assert route.rationale_source == "model"


def test_proposed_without_rationale_falls_back_to_honest_rule():
    events = [_ev("ActionProposed", ts_ms=2, tool_name="search_web", risk_tier="safe")]
    route = _step(build_decision_path(events), "route")
    assert route.rationale_source == "rule"
    assert "search_web" in route.rationale


def test_whitelist_approval_reads_as_plain_language():
    events = [_ev("ActionApproved", ts_ms=3, tool_name="x", approved_by="whitelist")]
    risk = _step(build_decision_path(events), "risk")
    assert risk.rationale_source == "rule"
    assert "allow-list" in risk.rationale.lower()


def test_user_approval_rationale():
    events = [_ev("ActionApproved", ts_ms=3, tool_name="x", approved_by="user")]
    risk = _step(build_decision_path(events), "risk")
    assert risk.rationale_source == "rule"
    assert "you approved" in risk.rationale.lower()


def test_denial_rule_rationale_carries_the_captured_reason():
    events = [_ev("ActionDenied", ts_ms=1, tool_name="rm", reason="blacklist: destructive")]
    risk = _step(build_decision_path(events), "risk")
    assert risk.rationale_source == "rule"
    assert "destructive" in risk.rationale


def test_brain_and_tier_steps_get_rule_rationale():
    events = [
        _ev("IntentClassified", ts_ms=1, intent="execute", risk_tier="ask"),
        _ev("BrainTurnStarted", ts_ms=4, provider="claude-api", model="opus"),
    ]
    steps = build_decision_path(events)
    assert _step(steps, "tier").rationale_source == "rule"
    brain = _step(steps, "brain")
    assert brain.rationale_source == "rule"
    assert "claude-api" in brain.rationale


def test_attach_tool_io_sets_command_and_output():
    events = [
        _ev("ToolCallStarted", ts_ms=1, tool_name="cli_gcloud",
            args_preview="gcloud projects list"),
        _ev("ToolCallCompleted", ts_ms=2, success=True, output_preview="3 projects found"),
    ]
    tools = [ToolCall(name="cli_gcloud")]
    attach_tool_io(events, tools)
    assert tools[0].command == "gcloud projects list"
    assert tools[0].output == "3 projects found"


def test_attach_tool_io_adds_missing_tool_from_events():
    # A tool seen only via ToolCallStarted/Completed (no ActionProposed) still
    # surfaces, so its command/result is never silently dropped.
    events = [
        _ev("ToolCallStarted", ts_ms=1, tool_name="read_file", args_preview="notes.md"),
        _ev("ToolCallCompleted", ts_ms=2, success=True, output_preview="(file body)"),
    ]
    tools: list[ToolCall] = []
    attach_tool_io(events, tools)
    assert any(t.name == "read_file" and t.command == "notes.md" for t in tools)
