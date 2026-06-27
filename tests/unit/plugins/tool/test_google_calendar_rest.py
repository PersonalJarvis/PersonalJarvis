"""google_calendar tool: a thin Python bridge over the JS/Node calendar bot.

These tests mock the Node call boundary (``node_runner``) so they run without
Node and without the network, the same way the Gmail tests mock the httpx
transport. They lock the contract: action → payload, 401 → one refresh + retry,
full autonomy (no action is ever ``ask``), and graceful "not connected" /
"node missing" behavior.
"""

import pytest

from jarvis.plugins.tool.google_calendar_rest import (
    GoogleCalendarRestTool,
    _default_node_runner,
)


def _ok(data):
    return {"ok": True, "data": data}


@pytest.mark.asyncio
async def test_list_events_passes_window_and_token():
    captured = {}

    async def runner(action, args, token):
        captured["action"] = action
        captured["args"] = args
        captured["bearer"] = token
        return _ok({"events": [{"id": "e1", "summary": "Standup"}]})

    tool = GoogleCalendarRestTool(
        access_token_provider=lambda: "at_123", node_runner=runner
    )
    out = await tool.list_events(time_min="2026-06-28T00:00:00", time_max="2026-06-28T23:59:59")
    assert out["data"]["events"][0]["id"] == "e1"
    assert captured["action"] == "list_events"
    assert captured["bearer"] == "at_123"
    assert captured["args"]["time_min"] == "2026-06-28T00:00:00"


@pytest.mark.asyncio
async def test_create_event_builds_payload():
    captured = {}

    async def runner(action, args, token):
        captured["action"] = action
        captured["args"] = args
        return _ok({"id": "new1", "summary": "Lunch"})

    tool = GoogleCalendarRestTool(
        access_token_provider=lambda: "at_123", node_runner=runner
    )
    out = await tool.create_event(
        summary="Lunch", start="2026-06-28T12:00:00", end="2026-06-28T13:00:00",
        time_zone="Europe/Berlin",
    )
    assert out["data"]["id"] == "new1"
    assert captured["action"] == "create_event"
    assert captured["args"]["summary"] == "Lunch"
    assert captured["args"]["time_zone"] == "Europe/Berlin"


@pytest.mark.asyncio
async def test_delete_event_passes_id():
    captured = {}

    async def runner(action, args, token):
        captured["action"] = action
        captured["args"] = args
        return _ok({"deleted": "e9"})

    tool = GoogleCalendarRestTool(
        access_token_provider=lambda: "at_123", node_runner=runner
    )
    out = await tool.delete_event(event_id="e9")
    assert out["data"]["deleted"] == "e9"
    assert captured["action"] == "delete_event"
    assert captured["args"]["event_id"] == "e9"


@pytest.mark.asyncio
async def test_delete_passes_calendar_id_for_secondary_calendar():
    # An event found by list_events on a non-primary calendar must be deletable
    # by threading its calendar_id back through — else delete hits 'primary' and
    # 404s. (Root cause of the "found nothing / can't touch it" lesson bug.)
    captured = {}

    async def runner(action, args, token):
        captured["args"] = args
        return _ok({"deleted": "e9", "calendar_id": "school@group.calendar.google.com"})

    tool = GoogleCalendarRestTool(
        access_token_provider=lambda: "at_123", node_runner=runner
    )
    out = await tool.execute(
        {"action": "delete_event", "event_id": "e9",
         "calendar_id": "school@group.calendar.google.com"},
        ctx=None,
    )
    assert out.success is True
    assert captured["args"]["calendar_id"] == "school@group.calendar.google.com"


@pytest.mark.asyncio
async def test_execute_returns_error_when_not_connected():
    tool = GoogleCalendarRestTool(access_token_provider=lambda: None)
    result = await tool.execute({"action": "list_events"}, ctx=None)
    assert result.success is False
    assert "connect" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_refreshes_on_401_then_retries():
    # An expired access token → the bot reports status 401. The bridge must
    # refresh once and retry — the same self-heal as Gmail.
    calls = {"n": 0, "refresh": 0}

    async def runner(action, args, token):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "status": 401, "error": "Calendar API 401"}
        return _ok({"events": [{"id": "ok9"}]})

    async def refresher():
        calls["refresh"] += 1
        return True

    tool = GoogleCalendarRestTool(
        access_token_provider=lambda: "at_dead",
        node_runner=runner,
        token_refresher=refresher,
    )
    out = await tool.list_events()
    assert out["data"]["events"][0]["id"] == "ok9"
    assert calls["refresh"] == 1
    assert calls["n"] == 2  # retried exactly once after a successful refresh


@pytest.mark.asyncio
async def test_returns_reconnect_error_when_refresh_fails():
    async def runner(action, args, token):
        return {"ok": False, "status": 401, "error": "Calendar API 401"}

    async def refresher():
        return False  # un-healable (revoked / invalid_client)

    tool = GoogleCalendarRestTool(
        access_token_provider=lambda: "at_dead",
        node_runner=runner,
        token_refresher=refresher,
    )
    result = await tool.execute({"action": "list_events"}, ctx=None)
    assert result.success is False
    assert "reconnect" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_non_401_error_is_not_retried():
    # A 500 is not an auth problem — do not refresh, do not retry.
    calls = {"n": 0, "refresh": 0}

    async def runner(action, args, token):
        calls["n"] += 1
        return {"ok": False, "status": 500, "error": "Calendar API 500"}

    async def refresher():
        calls["refresh"] += 1
        return True

    tool = GoogleCalendarRestTool(
        access_token_provider=lambda: "at_123",
        node_runner=runner,
        token_refresher=refresher,
    )
    result = await tool.execute({"action": "list_events"}, ctx=None)
    assert result.success is False
    assert calls["refresh"] == 0
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_execute_missing_required_fields():
    tool = GoogleCalendarRestTool(access_token_provider=lambda: "at_1")
    # create without summary
    r1 = await tool.execute({"action": "create_event", "start": "x", "end": "y"}, ctx=None)
    assert r1.success is False and "summary" in (r1.error or "")
    # update without id
    r2 = await tool.execute({"action": "update_event", "summary": "z"}, ctx=None)
    assert r2.success is False and "event_id" in (r2.error or "")
    # delete without id
    r3 = await tool.execute({"action": "delete_event"}, ctx=None)
    assert r3.success is False and "event_id" in (r3.error or "")


def test_tool_contract_shape():
    tool = GoogleCalendarRestTool()
    assert tool.name == "google_calendar"
    assert tool.risk_tier == "monitor"
    assert "schema" in dir(tool)


# ----------------------------------------------------------------------
# Full autonomy (user mandate): NO action is ever gated behind ``ask`` —
# read is safe, every write is monitor (executed without a prompt, audited).
# ----------------------------------------------------------------------


def test_risk_tier_read_is_safe():
    tool = GoogleCalendarRestTool(access_token_provider=lambda: "x")
    assert tool.risk_tier_for_args({"action": "list_events"}) == "safe"
    assert tool.risk_tier_for_args({}) == "safe"  # defaults to list_events


def test_risk_tier_writes_are_monitor_never_ask():
    tool = GoogleCalendarRestTool(access_token_provider=lambda: "x")
    for action in ("create_event", "update_event", "delete_event"):
        tier = tool.risk_tier_for_args({"action": action})
        assert tier == "monitor", f"{action} should be monitor, got {tier}"
        assert tier != "ask"


def test_risk_tier_unknown_action_is_not_safe():
    # An unrecognised action must not silently become safe; the monitor default
    # keeps an audit trail without a prompt.
    tool = GoogleCalendarRestTool(access_token_provider=lambda: "x")
    assert tool.risk_tier_for_args({"action": "purge_all"}) == "monitor"


@pytest.mark.asyncio
async def test_node_runner_graceful_when_node_missing(monkeypatch):
    # If `node` is not on PATH the bridge degrades to a clear error, never a crash.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    out = await _default_node_runner("list_events", {}, "at_1")
    assert out["ok"] is False
    assert "node" in out["error"].lower()
