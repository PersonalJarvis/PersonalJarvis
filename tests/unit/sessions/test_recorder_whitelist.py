"""The Run Inspector reads latency/decision/error events out of voice_events.
Guards that the recorder whitelist carries those kinds and their payload fields."""
from jarvis.sessions.recorder import _RAW_EVENT_KINDS, _payload_for
from jarvis.core.events import (
    IntentClassified, ActionProposed, ActionApproved, ActionDenied,
    ErrorOccurred, LatencySpan,
)


def test_decision_latency_error_kinds_are_whitelisted():
    for kind in (
        "IntentClassified", "ActionProposed", "ActionApproved",
        "ActionDenied", "ErrorOccurred", "LatencySpan",
    ):
        assert kind in _RAW_EVENT_KINDS


def test_payload_carries_decision_fields():
    p = _payload_for(ActionProposed(tool_name="cli_gcloud", risk_tier="ask"))
    assert p["tool_name"] == "cli_gcloud"
    assert p["risk_tier"] == "ask"
    p2 = _payload_for(ActionApproved(tool_name="cli_gcloud", approved_by="whitelist"))
    assert p2["approved_by"] == "whitelist"
    p3 = _payload_for(ActionDenied(tool_name="rm", reason="blacklist: destructive"))
    assert p3["reason"].startswith("blacklist")
    p4 = _payload_for(IntentClassified(intent="execute", risk_tier="monitor"))
    assert p4["intent"] == "execute" and p4["risk_tier"] == "monitor"


def test_payload_carries_latency_and_error_fields():
    p = _payload_for(LatencySpan(phase="intent_decision", duration_ms=42.0))
    assert p["phase"] == "intent_decision"
    assert p["duration_ms"] == 42.0
    e = _payload_for(ErrorOccurred(layer="brain", error_type="Timeout",
                                   message="provider chain unreachable", recoverable=False))
    assert e["layer"] == "brain" and e["error_type"] == "Timeout"
    assert e["message"].startswith("provider") and e["recoverable"] is False
