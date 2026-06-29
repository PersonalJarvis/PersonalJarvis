"""Run OUTCOME (success/partial/failed) is distinct from SLO latency, and run
ACTIVITY surfaces which tools/agents ran. The run list colored runs by latency,
so a slow-but-successful run looked failed (red); outcome fixes that."""
from jarvis.runs.analyzer import (
    build_activity,
    build_outcome,
    feature_tags_from_events,
    outcome_from_events,
    turn_outcome,
)
from jarvis.runs.constants import (
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_SUCCESS,
    RUN_OUTCOMES,
)
from jarvis.runs.model import DecisionStep, ErrorEntry, RunActivity, RunTurn, ToolCall
from jarvis.sessions.models import VoiceEventRow


def _turn(idx=0, jarvis_text="ok", tools=None, errors=None, decision=None):
    return RunTurn(idx=idx, trace_id=f"t{idx}", jarvis_text=jarvis_text,
                   tools=tools or [], errors=errors or [], decision_path=decision or [])


def _ev(kind, **payload):
    return VoiceEventRow(session_id="s", turn_id="t", ts_ms=0, kind=kind, payload=payload)


def test_outcomes_complete_and_stable():
    assert RUN_OUTCOMES == (OUTCOME_SUCCESS, OUTCOME_PARTIAL, OUTCOME_FAILED)
    assert set(RUN_OUTCOMES) == {"success", "partial", "failed"}


def test_answered_no_issues_is_success():
    assert turn_outcome(_turn(jarvis_text="Hallo")) == OUTCOME_SUCCESS
    assert build_outcome([_turn(jarvis_text="Hallo")]) == OUTCOME_SUCCESS


def test_tool_failure_but_answered_is_partial():
    t = _turn(jarvis_text="läuft",
              tools=[ToolCall(name="open_app", success=False, error_line="not found")])
    assert turn_outcome(t) == OUTCOME_PARTIAL


def test_hard_error_without_answer_is_failed():
    t = _turn(jarvis_text="",
              errors=[ErrorEntry(source="ErrorOccurred", message="chain down", recoverable=False)])
    assert turn_outcome(t) == OUTCOME_FAILED


def test_hard_error_but_answered_is_partial():
    t = _turn(jarvis_text="trotzdem geantwortet",
              errors=[ErrorEntry(source="ErrorOccurred", message="x", recoverable=False)])
    assert turn_outcome(t) == OUTCOME_PARTIAL


def test_build_outcome_is_worst_across_turns():
    turns = [_turn(idx=0, jarvis_text="ok"),
             _turn(idx=1, jarvis_text="ok",
                   tools=[ToolCall(name="click", success=False)])]
    assert build_outcome(turns) == OUTCOME_PARTIAL


def test_activity_detects_computer_use_and_sub_agent():
    t = _turn(tools=[ToolCall(name="cli_gcloud"), ToolCall(name="click_element", success=False)],
              decision=[DecisionStep(kind="mission", label="spawned sub-agent mission")])
    act = build_activity([t])
    assert isinstance(act, RunActivity)
    assert "cli_gcloud" in act.tools
    assert "computer_use" in act.agents   # click_element is a Computer-Use action
    assert "sub_agent" in act.agents       # a mission decision step


def test_outcome_from_events_slow_but_successful_is_success():
    # A successful answer with only a slow LatencySpan must NOT read as failed.
    events = [_ev("ResponseGenerated", text="Die Antwort."),
              _ev("LatencySpan", phase="brain_first_token", duration_ms=22000.0)]
    assert outcome_from_events(events) == OUTCOME_SUCCESS


def test_outcome_from_events_failed_tool_is_partial():
    events = [_ev("ResponseGenerated", text="läuft"),
              _ev("ActionExecuted", tool_name="open_app", success=False, error="not found")]
    assert outcome_from_events(events) == OUTCOME_PARTIAL


def test_feature_tags_from_events():
    events = [
        _ev("ActionProposed", tool_name="computer_use"),
        _ev("ActionProposed", tool_name="cli_gcloud"),
        _ev("JarvisAgentTaskStarted"),
    ]
    tags = feature_tags_from_events(events)
    assert "computer_use" in tags
    assert "sub_agent" in tags
    assert "cli_gcloud" in tags


def test_spawn_and_skill_tools_map_to_named_agents():
    # The spawn-worker / run-skill tools are surfaced as Sub-Agent / Skill agents,
    # not as raw tool chips (the maintainer asked for clear agent visualization).
    events = [_ev("ActionProposed", tool_name="spawn_worker"),
              _ev("ActionProposed", tool_name="run-skill")]
    tags = feature_tags_from_events(events)
    assert "sub_agent" in tags and "skill" in tags
    assert "spawn_worker" not in tags and "run-skill" not in tags

    act = build_activity([_turn(tools=[ToolCall(name="spawn_worker"), ToolCall(name="run-skill")])])
    assert "sub_agent" in act.agents and "skill" in act.agents
    assert "spawn_worker" not in act.tools
