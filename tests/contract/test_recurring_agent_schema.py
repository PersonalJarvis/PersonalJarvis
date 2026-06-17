"""Contract tests for the recurring trigger + agent action extension.

The Tasks section gains three capabilities driven by the user request:
  1. A recurring `every` trigger (hourly / daily / custom interval) — the
     previously-missing "wiederkehrende Intervalle".
  2. An agentic `agent` action (a prompt the brain executes with a set of
     enabled plugins) — "wie Claude's Scheduled Tasks".
  3. Per-plugin permission scopes (`read` / `write` / `full`) so the user
     pre-authorizes what an unattended run may do.

These follow the five-layer anti-drift pattern (single-source tuples in
schema.py, asserted here and mirrored in TypeScript).
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from jarvis.tasks import (
    ACTION_KINDS,
    PLUGIN_SCOPES,
    TRIGGER_TYPES,
    AgentAction,
    PluginGrant,
    SpeakAction,
    TaskSpec,
    TriggerEvery,
)


# ---------------------------------------------------------------------
# Recurring `every` trigger
# ---------------------------------------------------------------------

def test_trigger_every_hourly():
    spec = TaskSpec(
        title="Hourly ping",
        trigger=TriggerEvery(interval_seconds=3600.0),
        action=SpeakAction(text="tick"),
    )
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 3600.0
    assert spec.trigger.start_at is None


def test_trigger_every_with_start_at():
    """Daily-at-07:00 is modelled as interval=86400 anchored to a start_at."""
    spec = TaskSpec(
        title="Daily 7am",
        trigger=TriggerEvery(
            interval_seconds=86400.0,
            start_at="2026-06-18T07:00:00+02:00",
        ),
        action=SpeakAction(text="good morning"),
    )
    assert spec.trigger.start_at.startswith("2026-06-18")


def test_trigger_every_rejects_nonpositive_interval():
    with pytest.raises(ValidationError):
        TriggerEvery(interval_seconds=0)
    with pytest.raises(ValidationError):
        TriggerEvery(interval_seconds=-60)


def test_trigger_every_caps_interval_at_one_year():
    one_year = 366 * 24 * 3600
    TriggerEvery(interval_seconds=one_year)  # ok
    with pytest.raises(ValidationError):
        TriggerEvery(interval_seconds=one_year + 1)


def test_trigger_types_now_includes_every():
    assert TRIGGER_TYPES == ("after_delay", "at_time", "on_event", "every")


def test_cron_trigger_still_rejected():
    """We add `every` (interval), NOT a raw cron expression. Cron stays invalid."""
    adapter = TypeAdapter(TaskSpec)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "title": "cron",
            "trigger": {"type": "cron", "expr": "0 * * * *"},
            "action": {"kind": "speak", "text": "hi"},
        })


# ---------------------------------------------------------------------
# Agent action + plugin grants
# ---------------------------------------------------------------------

def test_agent_action_basic():
    spec = TaskSpec(
        title="Morning Briefing",
        trigger=TriggerEvery(interval_seconds=86400.0),
        action=AgentAction(
            prompt="Summarize today's calendar and unread mail.",
            plugin_grants=(
                PluginGrant(plugin_id="google-calendar", scope="read"),
                PluginGrant(plugin_id="gmail", scope="read"),
            ),
        ),
    )
    assert spec.action.kind == "agent"
    assert spec.action.prompt.startswith("Summarize")
    assert len(spec.action.plugin_grants) == 2
    assert spec.action.plugin_grants[0].plugin_id == "google-calendar"
    assert spec.action.plugin_grants[0].scope == "read"


def test_agent_action_defaults_to_no_plugins_and_auto_tier():
    action = AgentAction(prompt="just think")
    assert action.plugin_grants == ()
    assert action.model_tier == "auto"


def test_agent_action_requires_prompt():
    with pytest.raises(ValidationError):
        AgentAction(prompt="")


def test_action_kinds_now_includes_agent():
    assert ACTION_KINDS == ("harness_dispatch", "speak", "tool_call", "agent")


def test_plugin_grant_scopes_are_read_write_full():
    assert PLUGIN_SCOPES == ("read", "write", "full")
    for scope in PLUGIN_SCOPES:
        g = PluginGrant(plugin_id="gmail", scope=scope)
        assert g.scope == scope


def test_plugin_grant_rejects_unknown_scope():
    with pytest.raises(ValidationError):
        PluginGrant(plugin_id="gmail", scope="admin")  # type: ignore[arg-type]


def test_plugin_grant_defaults_to_read():
    g = PluginGrant(plugin_id="gmail")
    assert g.scope == "read"


def test_agent_action_full_roundtrip_json():
    """A scheduled agent task must survive JSON persistence (spec_json blob)."""
    spec = TaskSpec(
        title="Daily tweet",
        trigger=TriggerEvery(interval_seconds=86400.0,
                             start_at="2026-06-18T09:00:00+02:00"),
        action=AgentAction(
            prompt="Post a short uplifting tweet about building software.",
            plugin_grants=(PluginGrant(plugin_id="buffer", scope="write"),),
            model_tier="deep",
        ),
    )
    raw = spec.model_dump_json()
    back = TaskSpec.model_validate_json(raw)
    assert back.trigger.type == "every"
    assert back.action.kind == "agent"
    assert back.action.plugin_grants[0].scope == "write"
    assert back.action.model_tier == "deep"
