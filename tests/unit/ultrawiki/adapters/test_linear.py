"""The Linear pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: stable
per-issue ids (a re-run must upsert, not duplicate), a real deep link on every
item, archived and completed issues included, inline comments folded
chronologically with an honest marker when more exist, deterministic
oldest-to-newest streaming for the backfill checkpoint, bounded 429 handling,
and failure messages a user can act on that never carry the token.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import linear as lin
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Linear plugin, without touching the host's keyring."""

    class _Tokens:
        access = "lin_api_test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-linear",
        config={"integration_id": "plugin:linear"},
        secret_get=lambda _name: None,
    )


def _comment(iso: str, body: str, author: str = "Alice") -> dict[str, Any]:
    return {"body": body, "createdAt": iso, "user": {"name": author}}


def _issue(issue_id: str = "abc-1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": issue_id,
        "identifier": "ENG-7",
        "title": "Ship the wake-word fix",
        "description": "The wake word misses every third call.",
        "url": "https://linear.app/acme/issue/ENG-7/ship-the-wake-word-fix",
        "createdAt": "2026-03-01T10:00:00.000Z",
        "updatedAt": "2026-03-04T12:00:00.000Z",
        "archivedAt": None,
        "dueDate": "2026-04-01",
        "state": {"name": "In Progress"},
        "assignee": {"name": "Alice"},
        "creator": {"name": "Bob"},
        "team": {"id": "team-1", "key": "ENG", "name": "Engineering"},
        "project": {"name": "Voice"},
        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }
    base.update(overrides)
    return base


def _json(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def _transport(
    pages: list[list[dict[str, Any]]],
    *,
    rate_limit_first: int = 0,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake Linear workspace: one GraphQL endpoint, cursor-paged."""
    remaining_429 = {"n": rate_limit_first}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if remaining_429["n"] > 0:
            remaining_429["n"] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        variables = json.loads(request.content.decode())["variables"]
        after = variables.get("after")
        index = int(after) if after else 0
        nodes = pages[index] if index < len(pages) else []
        has_next = index + 1 < len(pages)
        return _json(
            {
                "data": {
                    "issues": {
                        "nodes": nodes,
                        "pageInfo": {"hasNextPage": has_next, "endCursor": str(index + 1)},
                    }
                }
            }
        )

    return httpx.MockTransport(handler)


async def _collect(
    transport: httpx.MockTransport, checkpoint: str | None = None
) -> list:
    return [
        item
        async for item in lin.linear_pull_adapter(_ctx(), checkpoint, transport=transport)
    ]


async def test_an_issue_becomes_a_linkable_dated_item():
    issue = _issue(
        comments={
            "nodes": [
                _comment("2026-03-02T09:00:00.000Z", "On it"),
                _comment("2026-03-01T11:00:00.000Z", "Can you take this?", author="Bob"),
            ],
            "pageInfo": {"hasNextPage": False},
        }
    )
    items = await _collect(_transport([[issue]]))
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == "issue:abc-1"
    # Evidence has to lead back to where it lives — mandatory from item one.
    assert item.permalink == "https://linear.app/acme/issue/ENG-7/ship-the-wake-word-fix"
    # WHEN IT HAPPENED, not when it was last touched.
    assert item.timestamp_utc == "2026-03-01T10:00:00.000Z"
    assert item.title == "Ship the wake-word fix"
    assert item.author_raw == "Bob"
    assert item.thread_key == "team-1"
    # The composed header keeps the context a bare description would lose.
    assert "Engineering · issue ENG-7 · In Progress" in item.body
    assert "project Voice" in item.body
    assert "assigned to Alice" in item.body
    assert "due 2026-04-01" in item.body
    assert "The wake word misses every third call." in item.body
    # Comments fold in chronologically, oldest first.
    assert item.body.index("Can you take this?") < item.body.index("On it")
    # The cursor rides on the key the sync runner advances.
    assert item.metadata["mtime_ns"] == lin._to_ns("2026-03-04T12:00:00.000Z")
    assert item.metadata["comments"] == 2


async def test_archived_issues_are_asked_for_and_labelled():
    seen: list[httpx.Request] = []
    archived = _issue(archivedAt="2026-03-10T08:00:00.000Z", state={"name": "Done"})
    items = await _collect(_transport([[archived]], seen=seen))
    assert items[0].metadata["archived"] is True
    assert "archived" in items[0].body
    # The query itself must ask for archived rows, or they silently vanish.
    query = json.loads(seen[0].content.decode())["query"]
    assert "includeArchived: true" in query


async def test_comments_beyond_the_inline_page_are_declared():
    issue = _issue(
        comments={
            "nodes": [_comment("2026-03-01T11:00:00.000Z", "first of many")],
            "pageInfo": {"hasNextPage": True},
        }
    )
    items = await _collect(_transport([[issue]]))
    assert "[more comments on Linear — open the link to read them]" in items[0].body


async def test_a_numeric_checkpoint_becomes_an_updated_at_filter():
    seen: list[httpx.Request] = []
    await _collect(
        _transport([[]], seen=seen),
        checkpoint=str(lin._to_ns("2026-03-04T12:00:00Z")),
    )
    variables = json.loads(seen[0].content.decode())["variables"]
    # Five minutes before the cursor: the overlap is what makes a boundary
    # safe; re-yielded items upsert as unchanged.
    assert variables["filter"] == {"updatedAt": {"gte": "2026-03-04T11:55:00Z"}}


async def test_a_backfill_checkpoint_resumes_strictly_after_it():
    first = _issue("aaa", createdAt="2026-03-01T10:00:00.000Z")
    second = _issue("bbb", createdAt="2026-03-02T10:00:00.000Z")
    items = await _collect(_transport([[first, second]]), checkpoint="issue:aaa")
    assert [item.external_id for item in items] == ["issue:bbb"]


async def test_an_unknown_checkpoint_shape_is_read_as_no_cursor():
    seen: list[httpx.Request] = []
    items = await _collect(_transport([[_issue()]], seen=seen), checkpoint="task:42")
    assert len(items) == 1
    variables = json.loads(seen[0].content.decode())["variables"]
    assert "filter" not in variables


async def test_cursor_pages_are_walked_to_the_end():
    pages = [
        [_issue("aaa", createdAt="2026-03-01T10:00:00.000Z")],
        [_issue("bbb", createdAt="2026-03-02T10:00:00.000Z")],
    ]
    seen: list[httpx.Request] = []
    items = await _collect(_transport(pages, seen=seen))
    assert len(items) == 2
    assert len(seen) == 2  # one request per page, then pageInfo said stop


async def test_items_stream_oldest_to_newest_deterministically():
    newest_first = [
        _issue("zzz", createdAt="2026-03-05T10:00:00.000Z"),
        _issue("aaa", createdAt="2026-02-01T10:00:00.000Z"),
    ]
    items = await _collect(_transport([newest_first]))
    assert [item.external_id for item in items] == ["issue:aaa", "issue:zzz"]


async def test_rate_limited_requests_wait_and_retry():
    seen: list[httpx.Request] = []
    items = await _collect(_transport([[_issue()]], rate_limit_first=1, seen=seen))
    assert len(items) == 1
    assert len(seen) == 2  # the 429, then the honoured retry


async def test_a_persistent_rate_limit_raises_an_honest_bounded_error():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(lin.LinearAdapterError, match="rate limit"):
        await _collect(httpx.MockTransport(handler))
    assert len(seen) == lin._MAX_ATTEMPTS  # bounded, never an endless retry loop


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"error":"Unauthorized"}')

    with pytest.raises(lin.LinearAdapterError) as excinfo:
        await _collect(httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "lin_api_test" not in message


async def test_a_graphql_auth_error_is_mapped_like_a_401():
    """Linear can answer HTTP 200 with an in-band authentication error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json(
            {
                "errors": [
                    {
                        "message": "Authentication required",
                        "extensions": {"code": "AUTHENTICATION_ERROR"},
                    }
                ]
            }
        )

    with pytest.raises(lin.LinearAdapterError, match="Reconnect"):
        await _collect(httpx.MockTransport(handler))


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    seen: list[httpx.Request] = []
    with pytest.raises(lin.LinearAdapterError, match="not connected"):
        await _collect(_transport([[]], seen=seen))
    assert seen == []  # refused before a single request left the machine


async def test_an_oversized_item_is_capped_with_a_marker():
    huge = _issue(description="x" * 1_100_000)
    items = await _collect(_transport([[huge]]))
    body = items[0].body
    assert body.endswith("[truncated — the full text lives at the link above]")
    assert len(body) < 1_100_000


async def test_a_row_without_a_permalink_is_skipped_not_faked():
    """Every item must deep-link back; a row that cannot is dropped."""
    items = await _collect(_transport([[_issue(url=""), _issue("keep")]]))
    assert [item.external_id for item in items] == ["issue:keep"]


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(lin.INTEGRATION_ID)
    assert spec is not None and spec.id == "linear"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(lin.INTEGRATION_ID, lin.linear_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:linear") is True
    finally:
        plugin_bridge.unregister_pull_adapter(lin.INTEGRATION_ID)
