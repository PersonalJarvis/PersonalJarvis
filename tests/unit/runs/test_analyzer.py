from jarvis.runs.analyzer import (
    classify_latency, build_latency, build_decision_path,
    build_errors, build_extras, build_analytics,
)
from jarvis.runs.model import RunTurn, LatencyEntry, TurnExtras
from jarvis.sessions.models import VoiceEventRow


def _ev(kind, ts_ms=0, **payload):
    return VoiceEventRow(session_id="s", turn_id="t1", ts_ms=ts_ms, kind=kind, payload=payload)


def test_classify_latency_thresholds():
    # intent_decision budget = 150ms: <120 ok, 120..150 warn, >150 breach.
    assert classify_latency("intent_decision", 100.0) == "ok"
    assert classify_latency("intent_decision", 130.0) == "warn"
    assert classify_latency("intent_decision", 200.0) == "breach"
    # phase with no budget is always ok.
    assert classify_latency("tts_first_chunk", 99999.0) == "ok"


def test_build_latency_from_events():
    events = [_ev("LatencySpan", phase="intent_decision", duration_ms=200.0)]
    entries = build_latency(events)
    assert entries[0].phase == "intent_decision"
    assert entries[0].slo_status == "breach"


def test_decision_path_reconstruction():
    events = [
        _ev("IntentClassified", ts_ms=1, intent="execute", risk_tier="ask"),
        _ev("ActionProposed", ts_ms=2, tool_name="cli_gcloud", risk_tier="ask"),
        _ev("ActionApproved", ts_ms=3, tool_name="cli_gcloud", approved_by="whitelist"),
        _ev("BrainTurnStarted", ts_ms=4, provider="claude-api", model="opus",
            intent_level="direct_action"),
    ]
    steps = build_decision_path(events)
    kinds = [s.kind for s in steps]
    assert "tier" in kinds and "risk" in kinds and "brain" in kinds
    risk = next(s for s in steps if s.kind == "risk")
    assert "whitelist" in (risk.detail or "")


def test_decision_path_denied_and_fallback():
    events = [
        _ev("ActionDenied", ts_ms=1, tool_name="rm", reason="blacklist: destructive"),
        _ev("BrainTurnStarted", ts_ms=2, provider="gemini", model="flash"),
        _ev("BrainTurnStarted", ts_ms=3, provider="grok", model="grok-2"),
    ]
    steps = build_decision_path(events)
    # two distinct providers across the turn -> a fallback step.
    assert any(s.kind == "fallback" for s in steps)
    assert any(s.kind == "risk" and "blacklist" in (s.detail or "") for s in steps)


def test_build_errors():
    events = [
        _ev("ErrorOccurred", layer="brain", error_type="Timeout",
            message="chain down", recoverable=False),
        _ev("ActionDenied", tool_name="rm", reason="blacklist: x"),
    ]
    errs = build_errors(events)
    sources = {e.source for e in errs}
    assert "ErrorOccurred" in sources and "ActionDenied" in sources


def test_build_extras_cache_and_context():
    events = [
        _ev("BrainTTFT", cache_hit=True),
        _ev("SpeechSpoken", spoken_kind="other", detail="endpoint=silence"),
    ]
    extras = build_extras(events, tokens_in=1234)
    assert extras.cache_hit is True
    assert extras.context_tokens == 1234
    assert extras.endpoint_reason == "silence"


def test_build_analytics_aggregates_and_worst_slo():
    turns = [
        RunTurn(idx=0, trace_id="a", provider="claude-api", cost_usd=0.01,
                think_ms=100, speak_ms=200,
                latency=[LatencyEntry(phase="intent_decision", duration_ms=1, slo_status="ok")]),
        RunTurn(idx=1, trace_id="b", provider="gemini", cost_usd=0.02,
                latency=[LatencyEntry(phase="ack_first_audio", duration_ms=9999, slo_status="breach")],
                extras=TurnExtras(interrupted=True)),
    ]
    a = build_analytics(turns, started_ms=0, ended_ms=1000)
    assert a.total_duration_s == 1.0
    assert a.cost_by_provider["claude-api"] == 0.01
    assert a.worst_slo_status == "breach"
    assert a.interruptions == 1
