from jarvis.runs.model import (
    Run, RunTurn, RunListItem, ToolCall, LatencyEntry,
    DecisionStep, ErrorEntry, TurnExtras, RunAnalytics, MissionRef,
)


def test_run_turn_defaults_are_safe():
    t = RunTurn(idx=0, trace_id="t1")
    assert t.timeline == [] and t.latency == [] and t.tools == []
    assert t.decision_path == [] and t.errors == []
    assert t.extras.interrupted is False


def test_enum_fields_are_plain_strings():
    # str, not Literal — an unknown value must not raise (BUG-008).
    le = LatencyEntry(phase="future_phase", duration_ms=1.0, slo_status="weird")
    assert le.slo_status == "weird"
    ds = DecisionStep(kind="future_kind", label="x")
    assert ds.kind == "future_kind"


def test_decision_step_rationale_defaults_and_set():
    # The honest "why" rides on DecisionStep. Default = none captured.
    ds = DecisionStep(kind="risk", label="approved: cli_gcloud")
    assert ds.rationale == "" and ds.rationale_source == ""
    ds2 = DecisionStep(
        kind="route", label="proposed: open_calendar",
        rationale="I open the calendar to check your schedule.",
        rationale_source="model",
    )
    assert ds2.rationale.startswith("I open the calendar")
    assert ds2.rationale_source == "model"


def test_tool_call_command_and_output_defaults_and_set():
    # The already-captured command + result, surfaced (was hidden before).
    tc = ToolCall(name="cli_gcloud")
    assert tc.command == "" and tc.output == ""
    tc2 = ToolCall(name="cli_gcloud", command="gcloud projects list",
                   output="3 projects")
    assert tc2.command == "gcloud projects list" and tc2.output == "3 projects"


def test_run_list_item_shape():
    item = RunListItem(
        session_id="s1", started_ms=1, ended_ms=2, duration_s=0.001,
        hangup_reason="idle_timeout", wake_source="voice", turn_count=1,
        total_cost_usd=0.0, error_count=0, slo_status="ok", preview="hi",
    )
    assert item.session_id == "s1" and item.slo_status == "ok"
