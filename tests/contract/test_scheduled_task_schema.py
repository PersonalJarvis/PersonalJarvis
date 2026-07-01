"""Contract tests for TaskSpec + Trigger + Action (Phase 5 Capability 4).

Mandate DoD: 'Tell me hello in 30 seconds' must be representable as a
TaskSpec. Trigger scope is limited to `after_delay`, `at_time`, `on_event`
— no cron (ADR-0003 / User-§8.3).
"""
from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from jarvis.tasks import (
    TRIGGER_TYPES,
    HarnessDispatchAction,
    RetryPolicy,
    SpeakAction,
    TaskSpec,
    ToolCallAction,
    TriggerAfterDelay,
    TriggerAtTime,
    TriggerOnEvent,
)


def test_trigger_types_scope():
    # Extended 2026-06-17: `every` added for recurring intervals (hourly/daily).
    # `after_delay`/`at_time` stay one-shot; `on_event` stays event-driven.
    assert TRIGGER_TYPES == ("after_delay", "at_time", "on_event", "every")


def test_mandate_example_in_30s_hallo():
    """DoD example straight from the mandate: 'Tell me hello in 30 seconds'."""
    spec = TaskSpec(
        title="Sag Hallo in 30s",
        trigger=TriggerAfterDelay(delay_seconds=30.0),
        action=SpeakAction(text="Hallo"),
    )
    assert spec.trigger.type == "after_delay"
    assert spec.action.kind == "speak"
    assert spec.retry_policy.max_attempts == 1          # Default
    assert isinstance(spec.id, UUID)


def test_at_time_trigger_with_iso_timestamp():
    spec = TaskSpec(
        title="Abend-Erinnerung",
        trigger=TriggerAtTime(iso_timestamp="2026-04-22T20:00:00+02:00"),
        action=SpeakAction(text="Feierabend."),
    )
    assert spec.trigger.iso_timestamp.startswith("2026-04-22")


def test_on_event_trigger_with_filter_expr():
    spec = TaskSpec(
        title="Bei neuer Tom-Mail antworten",
        trigger=TriggerOnEvent(
            event_name="OutlookMailArrived",
            filter_expr="sender == 'tom@example.com'",
            max_firings=None,                          # unbegrenzt
        ),
        action=HarnessDispatchAction(
            harness="computer-use",
            prompt="Oeffne Outlook und beantworte Toms neueste Mail freundlich.",
            allow_computer_use=True,
        ),
    )
    assert spec.trigger.type == "on_event"
    assert spec.action.kind == "harness_dispatch"


def test_rejects_cron_trigger():
    """There is deliberately NO cron trigger. Incorrect usage must fail."""
    adapter = TypeAdapter(TaskSpec)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "title": "jede Stunde",
            "trigger": {"type": "cron", "expr": "0 * * * *"},
            "action": {"kind": "speak", "text": "hi"},
        })


def test_rejects_unknown_action_kind():
    with pytest.raises(ValidationError):
        TaskSpec(
            title="test",
            trigger=TriggerAfterDelay(delay_seconds=1.0),
            action={"kind": "exec_shell", "cmd": "rm -rf /"},  # type: ignore[arg-type]
        )


def test_tool_call_action_accepts_free_args():
    spec = TaskSpec(
        title="Open Outlook",
        trigger=TriggerAfterDelay(delay_seconds=5.0),
        action=ToolCallAction(tool_name="open_app",
                              args={"app": "outlook"}),
    )
    assert spec.action.args["app"] == "outlook"


def test_delay_seconds_must_be_positive():
    with pytest.raises(ValidationError):
        TriggerAfterDelay(delay_seconds=0)
    with pytest.raises(ValidationError):
        TriggerAfterDelay(delay_seconds=-1)


def test_delay_seconds_capped_at_30_days():
    max_ok = 30 * 24 * 3600
    TriggerAfterDelay(delay_seconds=max_ok)             # ok
    with pytest.raises(ValidationError):
        TriggerAfterDelay(delay_seconds=max_ok + 1)


def test_retry_policy_defaults():
    rp = RetryPolicy()
    assert rp.max_attempts == 1
    assert rp.backoff_initial_s == 5.0
    assert rp.backoff_factor == 2.0
    assert rp.retry_on_interrupt is True


def test_retry_policy_bounds():
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=11)


def test_taskspec_extra_fields_rejected():
    adapter = TypeAdapter(TaskSpec)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "title": "x",
            "trigger": {"type": "after_delay", "delay_seconds": 1.0},
            "action": {"kind": "speak", "text": "hi"},
            "evil": True,
        })
