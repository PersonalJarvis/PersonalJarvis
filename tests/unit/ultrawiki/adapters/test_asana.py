"""The Asana pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: stable
per-task ids (a re-run must upsert, not duplicate), a real deep link on every
item, deterministic oldest-to-newest streaming so the backfill checkpoint can
resume strictly after an id, comment stories folded chronologically, bounded
429 handling, and failure messages a user can act on that never carry the
token.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import asana as asn
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Asana plugin, without touching the host's keyring."""

    class _Tokens:
        access = "asana-test-token"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-asana",
        config={"integration_id": "plugin:asana"},
        secret_get=lambda _name: None,
    )


def _task(gid: str = "t1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "gid": gid,
        "name": "Ship the wake-word fix",
        "notes": "The wake word misses every third call.",
        "completed": False,
        "created_at": "2026-03-01T10:00:00.000Z",
        "modified_at": "2026-03-04T12:00:00.000Z",
        "due_on": "2026-04-01",
        "assignee": {"name": "Alice"},
        "created_by": {"name": "Bob"},
        "permalink_url": f"https://app.asana.com/0/p1/{gid}",
    }
    base.update(overrides)
    return base


def _story(iso: str, text: str, author: str = "Alice", subtype: str = "comment_added"):
    return {
        "created_at": iso,
        "created_by": {"name": author},
        "resource_subtype": subtype,
        "text": text,
    }


def _json(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def _transport(
    *,
    workspaces: list[dict[str, Any]] | None = None,
    projects: dict[str, list[dict[str, Any]]] | None = None,
    project_tasks: dict[str, list[dict[str, Any]]] | None = None,
    my_tasks: dict[str, list[dict[str, Any]]] | None = None,
    stories: dict[str, list[dict[str, Any]]] | None = None,
    rate_limit_first: int = 0,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake Asana account: dispatches by REST path."""
    remaining_429 = {"n": rate_limit_first}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if remaining_429["n"] > 0:
            remaining_429["n"] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        path = request.url.path
        params = request.url.params
        if path == "/api/1.0/workspaces":
            return _json({"data": workspaces or [{"gid": "ws1", "name": "Acme"}]})
        if path.startswith("/api/1.0/workspaces/") and path.endswith("/projects"):
            ws_gid = path.split("/")[-2]
            return _json({"data": (projects or {"ws1": [{"gid": "p1", "name": "Widgets"}]}).get(
                ws_gid, []
            )})
        if path == "/api/1.0/tasks" and params.get("project"):
            return _json({"data": (project_tasks or {}).get(params["project"], [])})
        if path == "/api/1.0/tasks":
            return _json({"data": (my_tasks or {}).get(params.get("workspace") or "", [])})
        if path.startswith("/api/1.0/tasks/") and path.endswith("/stories"):
            gid = path.split("/")[-2]
            return _json({"data": (stories or {}).get(gid, [])})
        return httpx.Response(404, content=b"{}")

    return httpx.MockTransport(handler)


async def _collect(
    transport: httpx.MockTransport, checkpoint: str | None = None
) -> list:
    return [
        item
        async for item in asn.asana_pull_adapter(_ctx(), checkpoint, transport=transport)
    ]


async def test_a_task_becomes_a_linkable_dated_item():
    items = await _collect(
        _transport(
            project_tasks={"p1": [_task()]},
            stories={
                "t1": [
                    _story("2026-03-02T09:00:00.000Z", "On it"),
                    _story("2026-03-01T11:00:00.000Z", "Can you take this?", author="Bob"),
                    _story(
                        "2026-03-01T10:30:00.000Z",
                        "moved to section Doing",
                        subtype="section_changed",
                    ),
                ]
            },
        )
    )
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == "task:t1"
    # Evidence has to lead back to where it lives — mandatory from item one.
    assert item.permalink == "https://app.asana.com/0/p1/t1"
    # WHEN IT HAPPENED, not when it was last touched.
    assert item.timestamp_utc == "2026-03-01T10:00:00.000Z"
    assert item.title == "Ship the wake-word fix"
    assert item.author_raw == "Bob"
    assert item.thread_key == "p1"
    # The composed header keeps the context a bare notes field would lose.
    header = "Widgets · task · open · assigned to Alice · due 2026-04-01 · created by Bob"
    assert header in item.body
    assert "The wake word misses every third call." in item.body
    # Comments fold in chronologically; system activity is bookkeeping, not memory.
    body = item.body
    assert body.index("Can you take this?") < body.index("On it")
    assert "moved to section" not in body
    # The cursor rides on the key the sync runner advances.
    assert item.metadata["mtime_ns"] == asn._to_ns("2026-03-04T12:00:00.000Z")
    assert item.metadata["comments"] == 2


async def test_completed_and_personal_tasks_arrive_too():
    done = _task("t2", name="Old chore", completed=True, permalink_url="https://app.asana.com/0/x/t2")
    personal = _task("t3", name="Just mine", permalink_url="https://app.asana.com/0/x/t3")
    items = await _collect(
        _transport(project_tasks={"p1": [done]}, my_tasks={"ws1": [personal]})
    )
    by_id = {item.external_id: item for item in items}
    assert by_id["task:t2"].metadata["state"] == "completed"
    # A task living in no walked project still arrives, keyed to its workspace.
    assert by_id["task:t3"].thread_key == "ws1"
    assert "Acme" in by_id["task:t3"].body


async def test_a_task_seen_in_two_listings_arrives_once():
    task = _task()
    items = await _collect(
        _transport(project_tasks={"p1": [task]}, my_tasks={"ws1": [task]})
    )
    assert [item.external_id for item in items] == ["task:t1"]
    # The project walk noted it first, so the project stays its thread.
    assert items[0].thread_key == "p1"


async def test_items_stream_oldest_to_newest_deterministically():
    newer = _task("t9", created_at="2026-03-05T10:00:00.000Z")
    older = _task("t2", created_at="2026-02-01T10:00:00.000Z")
    items = await _collect(_transport(project_tasks={"p1": [newer, older]}))
    assert [item.external_id for item in items] == ["task:t2", "task:t9"]


async def test_a_numeric_checkpoint_narrows_every_task_listing():
    seen: list[httpx.Request] = []
    await _collect(
        _transport(seen=seen),
        checkpoint=str(asn._to_ns("2026-03-04T12:00:00Z")),
    )
    task_calls = [r for r in seen if r.url.path == "/api/1.0/tasks"]
    assert task_calls  # both the project walk and the assignee listing ran
    for call in task_calls:
        # Five minutes before the cursor: the overlap is what makes a
        # boundary safe; re-yielded items upsert as unchanged.
        assert call.url.params.get("modified_since") == "2026-03-04T11:55:00Z"


async def test_a_backfill_checkpoint_resumes_strictly_after_it():
    first = _task("t1", created_at="2026-03-01T10:00:00.000Z")
    second = _task("t2", created_at="2026-03-02T10:00:00.000Z")
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(project_tasks={"p1": [first, second]}, seen=seen),
        checkpoint="task:t1",
    )
    assert [item.external_id for item in items] == ["task:t2"]
    # The skipped task's comment stories are never fetched — resume must not
    # re-spend the budget on rows the store already holds.
    story_calls = [r for r in seen if r.url.path.endswith("/stories")]
    assert [r.url.path for r in story_calls] == ["/api/1.0/tasks/t2/stories"]


async def test_an_unknown_checkpoint_shape_is_read_as_no_cursor():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(project_tasks={"p1": [_task()]}, seen=seen),
        checkpoint="issue:4242",
    )
    assert len(items) == 1
    task_calls = [r for r in seen if r.url.path == "/api/1.0/tasks"]
    assert all(call.url.params.get("modified_since") is None for call in task_calls)


async def test_offset_pages_are_walked_to_the_end():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/1.0/workspaces":
            return _json({"data": [{"gid": "ws1", "name": "Acme"}]})
        if path.endswith("/projects"):
            return _json({"data": [{"gid": "p1", "name": "Widgets"}]})
        if path == "/api/1.0/tasks" and request.url.params.get("project"):
            if request.url.params.get("offset"):
                return _json({"data": [_task("t2")]})
            return _json({"data": [_task("t1")], "next_page": {"offset": "cursor-2"}})
        if path == "/api/1.0/tasks":
            return _json({"data": []})
        if path.endswith("/stories"):
            return _json({"data": []})
        return httpx.Response(404, content=b"{}")

    items = await _collect(httpx.MockTransport(handler))
    assert sorted(item.external_id for item in items) == ["task:t1", "task:t2"]


async def test_rate_limited_requests_wait_and_retry():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(project_tasks={"p1": [_task()]}, rate_limit_first=1, seen=seen)
    )
    assert len(items) == 1
    # The 429, then the honoured retry of the same first request.
    assert seen[0].url.path == seen[1].url.path == "/api/1.0/workspaces"


async def test_a_persistent_rate_limit_raises_an_honest_bounded_error():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(asn.AsanaAdapterError, match="rate limit"):
        await _collect(httpx.MockTransport(handler))
    assert len(seen) == asn._MAX_ATTEMPTS  # bounded, never an endless retry loop


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"errors":[{"message":"Not Authorized"}]}')

    with pytest.raises(asn.AsanaAdapterError) as excinfo:
        await _collect(httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "asana-test-token" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    seen: list[httpx.Request] = []
    with pytest.raises(asn.AsanaAdapterError, match="not connected"):
        await _collect(_transport(seen=seen))
    assert seen == []  # refused before a single request left the machine


async def test_an_oversized_item_is_capped_with_a_marker():
    huge = _task(notes="x" * 1_100_000)
    items = await _collect(_transport(project_tasks={"p1": [huge]}))
    body = items[0].body
    assert body.endswith("[truncated — the full text lives at the link above]")
    assert len(body) < 1_100_000


async def test_a_row_without_a_permalink_is_skipped_not_faked():
    """Every item must deep-link back; a row that cannot is dropped."""
    items = await _collect(
        _transport(project_tasks={"p1": [_task(permalink_url=""), _task("t5")]})
    )
    assert [item.external_id for item in items] == ["task:t5"]


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(asn.INTEGRATION_ID)
    assert spec is not None and spec.id == "asana"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(asn.INTEGRATION_ID, asn.asana_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:asana") is True
    finally:
        plugin_bridge.unregister_pull_adapter(asn.INTEGRATION_ID)
