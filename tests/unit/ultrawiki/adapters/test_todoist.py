"""The Todoist pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: stable
per-task ids (a re-run must upsert, not duplicate), a canonical deep link on
every item, the completed history folded in next to the active snapshot,
comments rendered chronologically, sync-token incrementals that degrade to a
full read when the token goes stale, bounded 429 handling, and failure
messages a user can act on that never carry the token.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from jarvis.ultrawiki.adapters import todoist as td
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Todoist plugin, without touching the host's keyring."""

    class _Tokens:
        access = "todoist-test-token"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-todoist",
        config={"integration_id": "plugin:todoist"},
        secret_get=lambda _name: None,
    )


def _task(task_id: str = "t1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": task_id,
        "content": "Ship the wake-word fix",
        "description": "The wake word misses every third call.",
        "project_id": "p1",
        "added_at": "2026-03-01T10:00:00.000000Z",
        "checked": False,
        "is_deleted": False,
        "due": {"date": "2026-04-01"},
        "responsible_uid": "u2",
    }
    base.update(overrides)
    return base


def _note(item_id: str, posted_at: str, content: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"n-{item_id}-{posted_at}",
        "item_id": item_id,
        "posted_at": posted_at,
        "content": content,
        "is_deleted": False,
    }
    base.update(overrides)
    return base


_PROJECTS = [{"id": "p1", "name": "Widgets"}]
_PEOPLE = [{"id": "u2", "full_name": "Alice"}]


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "projects": _PROJECTS,
        "collaborators": _PEOPLE,
        "items": [],
        "notes": [],
        "sync_token": "fresh-token",
    }
    base.update(overrides)
    return base


def _json(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def _sync_token_of(request: httpx.Request) -> str:
    form = parse_qs(request.content.decode())
    return form.get("sync_token", [""])[0]


def _transport(
    *,
    full: dict[str, Any] | None = None,
    delta: dict[str, Any] | None = None,
    completed_pages: list[list[dict[str, Any]]] | None = None,
    stale_tokens: bool = False,
    rate_limit_first: int = 0,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake Todoist account: the sync endpoint plus the completed archive."""
    remaining_429 = {"n": rate_limit_first}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if remaining_429["n"] > 0:
            remaining_429["n"] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        path = request.url.path
        if path == "/sync/v9/sync":
            cursor = _sync_token_of(request)
            if cursor == "*":
                return _json(full or _payload())
            if stale_tokens:
                return httpx.Response(400, content=b'{"error":"invalid sync_token"}')
            return _json(delta or _payload())
        if path == "/sync/v9/completed/get_all":
            pages = completed_pages or [[]]
            offset = int(request.url.params.get("offset") or 0)
            index = offset // td._COMPLETED_PAGE
            rows = pages[index] if index < len(pages) else []
            return _json({"items": rows})
        return httpx.Response(404, content=b"{}")

    return httpx.MockTransport(handler)


async def _collect(
    transport: httpx.MockTransport, checkpoint: str | None = None
) -> list:
    return [
        item
        async for item in td.todoist_pull_adapter(_ctx(), checkpoint, transport=transport)
    ]


async def test_a_task_becomes_a_linkable_dated_item():
    notes = [
        _note("t1", "2026-03-02T09:00:00.000000Z", "On it"),
        _note("t1", "2026-03-01T11:00:00.000000Z", "Can you take this?"),
        _note("t1", "2026-03-03T09:00:00.000000Z", "gone", is_deleted=True),
    ]
    items = await _collect(_transport(full=_payload(items=[_task()], notes=notes)))
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == "task:t1"
    # The Sync API carries no web URL; this is the canonical task address.
    assert item.permalink == "https://app.todoist.com/app/task/t1"
    # WHEN IT HAPPENED, not when it was last touched.
    assert item.timestamp_utc == "2026-03-01T10:00:00.000000Z"
    assert item.title == "Ship the wake-word fix"
    assert item.thread_key == "p1"
    assert item.author_raw == "Alice"
    # The composed header keeps the context a bare description would lose.
    assert "Widgets · task · open · assigned to Alice · due 2026-04-01" in item.body
    assert "The wake word misses every third call." in item.body
    # Comments fold in chronologically; a deleted note is not memory.
    assert item.body.index("Can you take this?") < item.body.index("On it")
    assert "gone" not in item.body
    # No true modified time exists; the newest honest signal drives the cursor.
    assert item.metadata["mtime_ns"] == td._to_ns("2026-03-02T09:00:00.000000Z")
    assert item.metadata["comments"] == 2


async def test_the_completed_history_arrives_next_to_the_snapshot():
    completed_row = {
        "task_id": "t9",
        "content": "Old chore",
        "project_id": "p1",
        "completed_at": "2026-02-01T08:00:00.000000Z",
        "item_object": _task(
            "t9",
            content="Old chore",
            added_at="2026-01-01T10:00:00.000000Z",
            checked=True,
            due=None,
            responsible_uid=None,
        ),
        "notes": [_note("t9", "2026-01-02T10:00:00.000000Z", "done and dusted")],
    }
    items = await _collect(
        _transport(full=_payload(items=[_task()]), completed_pages=[[completed_row]])
    )
    by_id = {item.external_id: item for item in items}
    assert set(by_id) == {"task:t1", "task:t9"}
    done = by_id["task:t9"]
    assert done.metadata["state"] == "completed"
    assert done.metadata["completed_at"] == "2026-02-01T08:00:00.000000Z"
    assert "done and dusted" in done.body


async def test_deleted_tasks_are_skipped_not_imported():
    items = await _collect(
        _transport(full=_payload(items=[_task(is_deleted=True), _task("t2")]))
    )
    assert [item.external_id for item in items] == ["task:t2"]


async def test_a_sync_token_checkpoint_asks_for_the_delta_only():
    seen: list[httpx.Request] = []
    delta = _payload(items=[_task("t7", checked=True)])
    items = await _collect(
        _transport(delta=delta, seen=seen), checkpoint="opaque-sync-token"
    )
    assert [item.external_id for item in items] == ["task:t7"]
    assert items[0].metadata["state"] == "completed"
    sync_calls = [r for r in seen if r.url.path == "/sync/v9/sync"]
    assert [_sync_token_of(r) for r in sync_calls] == ["opaque-sync-token"]
    # A delta already carries completed transitions; the archive walk would
    # only re-spend the budget.
    assert not any(r.url.path.endswith("/completed/get_all") for r in seen)


async def test_a_stale_sync_token_degrades_to_a_full_read():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(full=_payload(items=[_task()]), stale_tokens=True, seen=seen),
        checkpoint="stale-sync-token",
    )
    assert [item.external_id for item in items] == ["task:t1"]
    sync_calls = [r for r in seen if r.url.path == "/sync/v9/sync"]
    # The rejected token, then the honest fresh start.
    assert [_sync_token_of(r) for r in sync_calls] == ["stale-sync-token", "*"]


async def test_a_numeric_checkpoint_still_yields_the_full_snapshot():
    """The Sync API gives active tasks no modified time, so a nanosecond
    cursor cannot narrow the read — pretending it could would skip changes."""
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(full=_payload(items=[_task()]), seen=seen),
        checkpoint=str(td._to_ns("2026-03-04T12:00:00Z")),
    )
    assert len(items) == 1
    sync_calls = [r for r in seen if r.url.path == "/sync/v9/sync"]
    assert [_sync_token_of(r) for r in sync_calls] == ["*"]


async def test_a_backfill_checkpoint_resumes_strictly_after_it():
    first = _task("t1", added_at="2026-03-01T10:00:00.000000Z")
    second = _task("t2", added_at="2026-03-02T10:00:00.000000Z")
    items = await _collect(
        _transport(full=_payload(items=[first, second])), checkpoint="task:t1"
    )
    assert [item.external_id for item in items] == ["task:t2"]


async def test_items_stream_oldest_to_newest_deterministically():
    newer = _task("t9", added_at="2026-03-05T10:00:00.000000Z")
    older = _task("t2", added_at="2026-02-01T10:00:00.000000Z")
    items = await _collect(_transport(full=_payload(items=[newer, older])))
    assert [item.external_id for item in items] == ["task:t2", "task:t9"]


async def test_completed_pages_are_walked_to_the_end():
    def _row(task_id: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "content": f"chore {task_id}",
            "project_id": "p1",
            "completed_at": "2026-02-01T08:00:00.000000Z",
            "item_object": _task(task_id, content=f"chore {task_id}", checked=True, due=None),
        }

    pages = [
        [_row(f"c{i}") for i in range(td._COMPLETED_PAGE)],
        [_row("last")],
    ]
    seen: list[httpx.Request] = []
    items = await _collect(_transport(completed_pages=pages, seen=seen))
    assert len(items) == td._COMPLETED_PAGE + 1
    walk = [r for r in seen if r.url.path.endswith("/completed/get_all")]
    assert len(walk) == 2  # stopped after the short second page


async def test_rate_limited_requests_wait_and_retry():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(full=_payload(items=[_task()]), rate_limit_first=1, seen=seen)
    )
    assert len(items) == 1
    assert seen[0].url.path == seen[1].url.path == "/sync/v9/sync"


async def test_a_persistent_rate_limit_raises_an_honest_bounded_error():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(td.TodoistAdapterError, match="rate limit"):
        await _collect(httpx.MockTransport(handler))
    assert len(seen) == td._MAX_ATTEMPTS  # bounded, never an endless retry loop


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"error":"Unauthorized"}')

    with pytest.raises(td.TodoistAdapterError) as excinfo:
        await _collect(httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "todoist-test-token" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    seen: list[httpx.Request] = []
    with pytest.raises(td.TodoistAdapterError, match="not connected"):
        await _collect(_transport(seen=seen))
    assert seen == []  # refused before a single request left the machine


async def test_an_oversized_item_is_capped_with_a_marker():
    huge = _task(description="x" * 1_100_000)
    items = await _collect(_transport(full=_payload(items=[huge])))
    body = items[0].body
    assert body.endswith("[truncated — the full text lives at the link above]")
    assert len(body) < 1_100_000


async def test_a_task_without_a_title_is_skipped_not_faked():
    items = await _collect(
        _transport(full=_payload(items=[_task(content=""), _task("t5")]))
    )
    assert [item.external_id for item in items] == ["task:t5"]


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(td.INTEGRATION_ID)
    assert spec is not None and spec.id == "todoist"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(td.INTEGRATION_ID, td.todoist_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:todoist") is True
    finally:
        plugin_bridge.unregister_pull_adapter(td.INTEGRATION_ID)
