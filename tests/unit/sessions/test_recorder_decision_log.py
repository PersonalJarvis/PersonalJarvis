"""The Run Inspector + local diary read the decision-log fields out of
voice_events. Guards that the recorder whitelist carries the brain rationale
(ActionProposed.rationale) and the tool output preview (ActionExecuted.output_preview)
so they survive into the persisted event stream."""
from jarvis.core.events import ActionExecuted, ActionProposed
from jarvis.sessions.recorder import _payload_for


def test_payload_carries_rationale_from_action_proposed():
    p = _payload_for(ActionProposed(
        tool_name="cli_gcloud", risk_tier="ask",
        rationale="You asked for spend, so I call the billing CLI.",
    ))
    assert p["rationale"] == "You asked for spend, so I call the billing CLI."


def test_payload_carries_output_preview_from_action_executed():
    p = _payload_for(ActionExecuted(
        tool_name="cli_gcloud", success=True, duration_ms=120,
        output_preview="Billing for project alpha: 12.40 EUR",
    ))
    assert p["output_preview"] == "Billing for project alpha: 12.40 EUR"


def test_empty_rationale_is_harmless():
    # No rationale -> empty string in the payload (the recorder skips None, not
    # "", and must keep recoverable=False). An empty rationale is just absent
    # signal, never raw noise.
    p = _payload_for(ActionProposed(tool_name="cli_gcloud", risk_tier="safe"))
    assert p.get("rationale", "") == ""
