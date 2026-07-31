"""The Google Calendar pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: a
deterministic calendar-by-calendar walk (sorted, so the resume convention
holds), calendar-scoped external ids with the real ``htmlLink`` deep link,
all-day events normalised to machine-comparable timestamps, recurring
instances threaded by their series, cancelled tombstones skipped, and the
``updatedMin`` narrowing that stands in for the syncToken the bridge's single
numeric cursor cannot hold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from jarvis.ultrawiki.adapters import _google as g
from jarvis.ultrawiki.adapters import google_calendar as gc
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Google Calendar plugin, without touching the host's keyring."""

    class _Tokens:
        access = "ya29.test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-gcal",
        config={"integration_id": "plugin:google_calendar"},
        secret_get=lambda _name: None,
    )


def _calendar(calendar_id: str, summary: str = "") -> dict[str, Any]:
    return {"id": calendar_id, "summary": summary or calendar_id}


def _event(
    event_id: str,
    start_iso: str,
    *,
    summary: str = "Standup",
    description: str = "",
    attendees: list[dict[str, Any]] | None = None,
    recurring: str = "",
    status: str = "confirmed",
    updated: str = "2026-03-05T08:00:00Z",
    all_day: bool = False,
    location: str = "",
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": event_id,
        "status": status,
        "htmlLink": f"https://calendar.google.com/calendar/event?eid={event_id}",
        "summary": summary,
        "updated": updated,
        "organizer": {"displayName": "Ada", "email": "ada@example.test"},
        "start": {"date": start_iso} if all_day else {"dateTime": start_iso},
    }
    if description:
        event["description"] = description
    if attendees is not None:
        event["attendees"] = attendees
    if recurring:
        event["recurringEventId"] = recurring
    if location:
        event["location"] = location
    return event


def _transport(
    *,
    calendars: list[dict[str, Any]],
    events: dict[str, list[list[dict[str, Any]]]],
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake Calendar API: a calendar list plus paged per-calendar events."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = unquote(request.url.path)
        if path == "/calendar/v3/users/me/calendarList":
            return httpx.Response(200, json={"items": calendars})
        if path.startswith("/calendar/v3/calendars/") and path.endswith("/events"):
            calendar_id = path[len("/calendar/v3/calendars/") : -len("/events")]
            pages = events.get(calendar_id, [[]])
            index = int(request.url.params.get("pageToken") or 0)
            page = pages[index] if index < len(pages) else []
            payload: dict[str, Any] = {"items": page}
            if index + 1 < len(pages):
                payload["nextPageToken"] = str(index + 1)
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _collect(transport: httpx.MockTransport, checkpoint: str | None = None) -> list:
    return [
        item
        async for item in gc.google_calendar_pull_adapter(
            _ctx(), checkpoint, transport=transport
        )
    ]


async def test_every_calendar_contributes_in_deterministic_sorted_order():
    items = await _collect(
        _transport(
            # The API answers in ITS order; the walk must not depend on it.
            calendars=[_calendar("b@x", "Team"), _calendar("a@x", "Personal")],
            events={
                "a@x": [[_event("e1", "2026-03-01T09:00:00Z")]],
                "b@x": [[_event("e2", "2026-03-02T09:00:00Z")]],
            },
        )
    )
    # Calendar-scoped ids: the same invitation on two calendars must upsert as
    # two rows, and the sorted calendar order is what the resume leans on.
    assert [item.external_id for item in items] == ["a@x:e1", "b@x:e2"]
    first = items[0]
    assert first.permalink == "https://calendar.google.com/calendar/event?eid=e1"
    assert first.title == "Standup"
    assert first.timestamp_utc == "2026-03-01T09:00:00Z"
    assert first.author_raw == "Ada"
    assert "Personal · event · 2026-03-01T09:00:00Z" in first.body
    # The cursor rides on the key the sync runner advances.
    assert first.metadata["mtime_ns"] == g.to_ns("2026-03-05T08:00:00Z")


async def test_an_all_day_event_gets_a_machine_comparable_midnight_timestamp():
    items = await _collect(
        _transport(
            calendars=[_calendar("a@x")],
            events={"a@x": [[_event("e1", "2026-03-01", all_day=True)]]},
        )
    )
    assert items[0].timestamp_utc == "2026-03-01T00:00:00Z"


async def test_cancelled_events_are_skipped():
    items = await _collect(
        _transport(
            calendars=[_calendar("a@x")],
            events={
                "a@x": [
                    [
                        _event("e1", "2026-03-01T09:00:00Z", status="cancelled"),
                        _event("e2", "2026-03-02T09:00:00Z"),
                    ]
                ]
            },
        )
    )
    # A tombstone carries no text to remember.
    assert [item.external_id for item in items] == ["a@x:e2"]


async def test_recurring_instances_thread_together_as_one_series():
    items = await _collect(
        _transport(
            calendars=[_calendar("a@x")],
            events={
                "a@x": [
                    [
                        _event("s1_20260301", "2026-03-01T09:00:00Z", recurring="s1"),
                        _event("s1_20260308", "2026-03-08T09:00:00Z", recurring="s1"),
                    ]
                ]
            },
        )
    )
    assert [item.thread_key for item in items] == ["a@x:s1", "a@x:s1"]
    assert items[0].external_id != items[1].external_id


async def test_a_large_attendee_list_is_summarised_not_dumped():
    attendees = [{"email": f"person{i:02d}@example.test"} for i in range(30)]
    items = await _collect(
        _transport(
            calendars=[_calendar("a@x")],
            events={"a@x": [[_event("e1", "2026-03-01T09:00:00Z", attendees=attendees)]]},
        )
    )
    body = items[0].body
    assert "Attendees (30):" in body
    assert "(+5 more)" in body  # 25 shown, the rest counted — not a mailing list dump
    assert "person29@example.test" not in body


async def test_an_html_description_is_stripped_to_readable_text():
    items = await _collect(
        _transport(
            calendars=[_calendar("a@x")],
            events={
                "a@x": [
                    [
                        _event(
                            "e1",
                            "2026-03-01T09:00:00Z",
                            description="<p>Agenda&nbsp;items</p><p>Budget</p>",
                        )
                    ]
                ]
            },
        )
    )
    body = items[0].body
    assert "Agenda items" in body
    assert "Budget" in body
    assert "<p>" not in body


async def test_event_pages_are_walked_to_the_end():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            calendars=[_calendar("a@x")],
            events={
                "a@x": [
                    [_event("e1", "2026-03-01T09:00:00Z")],
                    [_event("e2", "2026-03-02T09:00:00Z")],
                ]
            },
            seen=seen,
        )
    )
    assert [item.external_id for item in items] == ["a@x:e1", "a@x:e2"]
    event_calls = [r for r in seen if r.url.path.endswith("/events")]
    assert len(event_calls) == 2


async def test_the_walk_asks_for_expanded_instances_in_start_order():
    seen: list[httpx.Request] = []
    await _collect(
        _transport(calendars=[_calendar("a@x")], events={"a@x": [[]]}, seen=seen)
    )
    call = next(r for r in seen if r.url.path.endswith("/events"))
    params = call.url.params
    assert params.get("singleEvents") == "true"
    assert params.get("orderBy") == "startTime"
    assert params.get("timeMin") == "1970-01-01T00:00:00Z"
    assert params.get("timeMax")  # the recurring-expansion ceiling is bounded


async def test_a_numeric_checkpoint_becomes_updated_min_a_day_earlier():
    ns = g.to_ns("2026-03-04T12:00:00Z")
    seen: list[httpx.Request] = []
    await _collect(
        _transport(calendars=[_calendar("a@x")], events={"a@x": [[]]}, seen=seen),
        checkpoint=str(ns),
    )
    call = next(r for r in seen if r.url.path.endswith("/events"))
    # One day earlier, so a boundary can never skip an event; the rewound
    # overlap upserts as unchanged. This is the documented syncToken fallback.
    assert call.url.params.get("updatedMin") == "2026-03-03T12:00:00Z"


async def test_a_backfill_checkpoint_skips_completed_calendars():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            calendars=[_calendar("a@x"), _calendar("b@x")],
            events={
                "a@x": [[_event("e1", "2026-03-01T09:00:00Z")]],
                "b@x": [[_event("e2", "2026-03-02T09:00:00Z")]],
            },
            seen=seen,
        ),
        checkpoint="b@x:e2",
    )
    # Calendars sorted before the checkpoint's calendar are complete; the
    # checkpoint's own calendar is re-walked (cheap — pages carry full data).
    walked = {
        unquote(r.url.path)[len("/calendar/v3/calendars/") : -len("/events")]
        for r in seen
        if r.url.path.endswith("/events")
    }
    assert walked == {"b@x"}
    assert [item.external_id for item in items] == ["b@x:e2"]


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    seen: list[httpx.Request] = []
    with pytest.raises(gc.GoogleAdapterError, match="not connected"):
        await _collect(_transport(calendars=[], events={}, seen=seen))
    assert seen == []  # refused before a single request left the machine


async def test_an_oversized_description_is_capped_and_marked():
    huge = _event("e1", "2026-03-01T09:00:00Z", description="x" * (g.BODY_CAP + 10))
    items = await _collect(
        _transport(calendars=[_calendar("a@x")], events={"a@x": [[huge]]})
    )
    assert items[0].body.endswith(g.TRUNCATION_MARKER)
    assert items[0].metadata["truncated"] is True


async def test_the_lookahead_ceiling_rolls_forward_with_the_sync():
    seen: list[httpx.Request] = []
    await _collect(
        _transport(calendars=[_calendar("a@x")], events={"a@x": [[]]}, seen=seen)
    )
    call = next(r for r in seen if r.url.path.endswith("/events"))
    ceiling = datetime.fromisoformat(
        call.url.params.get("timeMax").replace("Z", "+00:00")
    )
    days_ahead = (ceiling - datetime.now(UTC)).days
    # Two years ahead of THIS sync — instances beyond it arrive as time does.
    assert 725 <= days_ahead <= 731


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(gc.INTEGRATION_ID)
    assert spec is not None and spec.id == "google_calendar"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(
        gc.INTEGRATION_ID, gc.google_calendar_pull_adapter
    )
    try:
        assert plugin_bridge.has_pull_adapter("plugin:google_calendar") is True
    finally:
        plugin_bridge.unregister_pull_adapter(gc.INTEGRATION_ID)
