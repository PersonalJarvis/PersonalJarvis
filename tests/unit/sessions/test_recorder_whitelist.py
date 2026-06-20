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


def test_payload_extracts_safe_action_but_never_pii_args():
    # ActionProposed.args may hold PII (recipient, subject, body, search query).
    # Only the short enum-like `action` selector is persisted so forensics can
    # tell e.g. a gmail read from a send — the raw args must NEVER reach the DB
    # (forensic 2026-06-19: the action was unrecoverable from the persisted
    # event, which is itself the bug being closed here).
    p = _payload_for(ActionProposed(
        tool_name="gmail", risk_tier="ask",
        args={
            "action": "send_message",
            "to": "alice@example.com",
            "subject": "secret subject",
            "body": "private body text",
        },
    ))
    assert p["action"] == "send_message"
    assert p["tool_name"] == "gmail"
    # No raw args dict, no PII fields, no PII values anywhere in the payload.
    assert "args" not in p
    assert "to" not in p and "subject" not in p and "body" not in p
    blob = str(p)
    assert "alice@example.com" not in blob
    assert "private body text" not in blob
    assert "secret subject" not in blob


def test_payload_omits_action_when_args_have_no_action():
    # A tool call without an `action` key adds no action field (no empty noise),
    # and a PII-ish free-text arg like a search query is never persisted.
    p = _payload_for(ActionProposed(tool_name="search_web", args={"query": "my private search"}))
    assert "action" not in p
    assert "query" not in p
    assert "my private search" not in str(p)


def test_payload_ignores_non_string_or_oversized_action():
    # Defensive: a non-string or absurdly long `action` is not persisted, so a
    # tool that overloads the key cannot smuggle a payload into the DB.
    p = _payload_for(ActionProposed(tool_name="weird", args={"action": 12345}))
    assert "action" not in p
    p2 = _payload_for(ActionProposed(tool_name="weird", args={"action": "x" * 500}))
    assert "action" not in p2
