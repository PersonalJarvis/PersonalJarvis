"""The Notion pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: stable
per-page and per-row ids (a re-run must upsert, not duplicate), Notion's own
deep link on every item, faithful plain-text rendering of the block tree with
bounded depth and a cycle guard, per-database degradation instead of
all-or-nothing failure, bounded 429 handling, and failure messages a user can
act on that never carry the token.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import notion as no
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Notion plugin, without touching the host's keyring."""

    class _Tokens:
        access = "ntn-test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-notion",
        config={"integration_id": "plugin:notion"},
        secret_get=lambda _name: None,
    )


def _rt(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "plain_text": text}]


def _page_obj(
    pid: str,
    title: str = "Untitled",
    edited: str = "2026-03-01T10:00:00.000Z",
    created: str = "2026-02-01T09:00:00.000Z",
    parent: dict[str, Any] | None = None,
    props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {"Name": {"id": "title", "type": "title", "title": _rt(title)}}
    properties.update(props or {})
    return {
        "object": "page",
        "id": pid,
        "created_time": created,
        "last_edited_time": edited,
        "parent": parent or {"type": "workspace", "workspace": True},
        "url": f"https://www.notion.so/{pid}",
        "properties": properties,
    }


def _db_obj(
    did: str, title: str = "Tasks", edited: str = "2026-03-01T08:00:00.000Z"
) -> dict[str, Any]:
    return {
        "object": "database",
        "id": did,
        "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": edited,
        "title": _rt(title),
        "url": f"https://www.notion.so/{did}",
    }


def _block(
    bid: str, btype: str, text: str = "", has_children: bool = False, **payload: Any
) -> dict[str, Any]:
    content: dict[str, Any] = {"rich_text": _rt(text) if text else []}
    content.update(payload)
    return {
        "object": "block",
        "id": bid,
        "type": btype,
        "has_children": has_children,
        btype: content,
    }


def _json(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def _paged(pages: list[list[dict[str, Any]]], index: int) -> httpx.Response:
    page = pages[index] if index < len(pages) else []
    more = index + 1 < len(pages)
    return _json(
        {
            "object": "list",
            "results": page,
            "has_more": more,
            "next_cursor": str(index + 1) if more else None,
        }
    )


def _transport(
    *,
    search: list[list[dict[str, Any]]] | None = None,
    queries: dict[str, list[list[dict[str, Any]]]] | None = None,
    blocks: dict[str, list[list[dict[str, Any]]]] | None = None,
    query_errors: dict[str, int] | None = None,
    me_status: int = 200,
    rate_limit_first: int = 0,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake shared Notion workspace: dispatches by API path."""
    remaining_429 = {"n": rate_limit_first}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if remaining_429["n"] > 0:
            remaining_429["n"] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        path = request.url.path
        if path == "/v1/users/me":
            if me_status != 200:
                return httpx.Response(me_status)
            return _json({"object": "user", "type": "bot", "name": "Test Bot"})
        if path == "/v1/search":
            body = json.loads(request.content or b"{}")
            return _paged(search or [[]], int(body.get("start_cursor") or 0))
        if path.startswith("/v1/databases/") and path.endswith("/query"):
            did = path.split("/")[3]
            if did in (query_errors or {}):
                return httpx.Response((query_errors or {})[did])
            body = json.loads(request.content or b"{}")
            return _paged((queries or {}).get(did, [[]]), int(body.get("start_cursor") or 0))
        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            bid = path.split("/")[3]
            index = int(request.url.params.get("start_cursor") or 0)
            return _paged((blocks or {}).get(bid, [[]]), index)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _collect(transport: httpx.MockTransport, checkpoint: str | None = None) -> list:
    return [
        item
        async for item in no.notion_pull_adapter(_ctx(), checkpoint, transport=transport)
    ]


async def test_a_page_becomes_one_item_with_rendered_blocks():
    page = _page_obj("page-1", "Launch plan", edited="2026-03-01T10:00:00.000Z")
    root = [
        _block("b1", "heading_1", "Plan"),
        _block("b2", "paragraph", "We ship on Friday."),
        _block("b3", "bulleted_list_item", "pack the build"),
        _block("b4", "numbered_list_item", "first step"),
        _block("b5", "numbered_list_item", "second step"),
        _block("b6", "to_do", "write docs", checked=True),
        _block("b7", "to_do", "record demo", checked=False),
        _block("b8", "quote", "measure twice"),
        _block("b9", "code", "print('hi')", language="python"),
        _block("b10", "callout", "watch the deadline"),
        _block("b11", "toggle", "details", has_children=True),
    ]
    toggle_children = [_block("b12", "paragraph", "hidden note")]
    items = await _collect(
        _transport(search=[[page]], blocks={"page-1": [root], "b11": [toggle_children]})
    )
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == "page-1"
    assert item.title == "Launch plan"
    assert item.permalink == "https://www.notion.so/page-1"
    assert item.timestamp_utc == "2026-03-01T10:00:00.000Z"
    body = item.body
    assert "# Plan" in body
    assert "We ship on Friday." in body
    assert "- pack the build" in body
    assert "1. first step" in body and "2. second step" in body
    assert "[x] write docs" in body and "[ ] record demo" in body
    assert "> measure twice" in body
    assert "```python\nprint('hi')\n```" in body
    assert "[callout] watch the deadline" in body
    # The toggle's hidden children are rendered, indented one level deeper.
    assert "\n  hidden note" in body
    assert item.metadata["kind"] == "page"
    assert item.metadata["mtime_ns"] == no._to_ns("2026-03-01T10:00:00.000Z")


async def test_database_rows_flatten_their_properties():
    db = _db_obj("db-1", "Projects")
    row = _page_obj(
        "row-1",
        "Apollo",
        edited="2026-03-02T12:00:00.000Z",
        parent={"type": "database_id", "database_id": "db-1"},
        props={
            "Status": {"type": "status", "status": {"name": "Done"}},
            "Priority": {"type": "select", "select": {"name": "High"}},
            "Tags": {"type": "multi_select", "multi_select": [{"name": "a"}, {"name": "b"}]},
            "Due": {"type": "date", "date": {"start": "2026-03-05"}},
            "Shipped": {"type": "checkbox", "checkbox": True},
            "Estimate": {"type": "number", "number": 3},
        },
    )
    items = await _collect(_transport(search=[[db]], queries={"db-1": [[row]]}))
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "row-1"
    assert item.title == "Apollo"
    assert item.thread_key == "db-1"
    assert item.timestamp_utc == "2026-03-02T12:00:00.000Z"
    body = item.body
    assert "Projects · database row" in body
    assert "Status: Done" in body
    assert "Priority: High" in body
    assert "Tags: a, b" in body
    assert "Due: 2026-03-05" in body
    assert "Shipped: yes" in body
    assert "Estimate: 3" in body
    assert item.metadata["kind"] == "database_row"
    assert item.metadata["database_title"] == "Projects"


async def test_a_row_seen_via_search_and_query_is_yielded_once():
    db = _db_obj("db-1", "Projects")
    row = _page_obj(
        "row-1", "Apollo", parent={"type": "database_id", "database_id": "db-1"}
    )
    # Search returns BOTH the database and its row page; the row must not
    # import twice under the same id.
    items = await _collect(_transport(search=[[db, row]], queries={"db-1": [[row]]}))
    assert [item.external_id for item in items] == ["row-1"]


async def test_an_unqueryable_database_degrades_to_its_search_rows():
    """A linked or restricted database refuses its query; a row page the token
    can see still arrives via search and must import, not vanish."""
    db = _db_obj("db-1", "Projects")
    row = _page_obj(
        "row-1", "Apollo", parent={"type": "database_id", "database_id": "db-1"}
    )
    items = await _collect(_transport(search=[[db, row]], query_errors={"db-1": 404}))
    assert [item.external_id for item in items] == ["row-1"]
    assert items[0].thread_key == "db-1"
    assert items[0].metadata["kind"] == "database_row"


async def test_nested_blocks_stop_at_the_depth_cap_with_a_marker():
    page = _page_obj("page-1", "Deep")
    blocks: dict[str, list[list[dict[str, Any]]]] = {}
    parent = "page-1"
    for level in range(10):
        bid = f"chain-{level}"
        blocks[parent] = [[_block(bid, "paragraph", f"level{level}", has_children=True)]]
        parent = bid
    blocks[parent] = [[_block("tail", "paragraph", "level10")]]
    items = await _collect(_transport(search=[[page]], blocks=blocks))
    body = items[0].body
    assert "level7" in body
    assert "level8" not in body  # beyond the bounded depth
    assert no._DEPTH_MARKER in body


async def test_a_self_referencing_block_does_not_loop_the_import():
    page = _page_obj("page-1", "Loop")
    loop_block = _block("loop", "toggle", "spin", has_children=True)
    items = await _collect(
        _transport(
            search=[[page]],
            blocks={"page-1": [[loop_block]], "loop": [[loop_block]]},
        )
    )
    assert len(items) == 1  # terminated — the cycle guard broke the recursion
    assert "spin" in items[0].body


async def test_search_and_block_pages_are_walked_to_the_end():
    pages = [
        [_page_obj("p1", "One", edited="2026-03-01T10:00:00.000Z")],
        [_page_obj("p2", "Two", edited="2026-03-02T10:00:00.000Z")],
    ]
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            search=pages,
            blocks={
                "p1": [
                    [_block("b1", "paragraph", "from page one")],
                    [_block("b2", "paragraph", "from block page two")],
                ]
            },
            seen=seen,
        )
    )
    assert [item.external_id for item in items] == ["p1", "p2"]
    assert "from page one" in items[0].body
    assert "from block page two" in items[0].body
    search_calls = [r for r in seen if r.url.path == "/v1/search"]
    assert len(search_calls) == 2
    block_calls = [r for r in seen if r.url.path == "/v1/blocks/b1/children"]
    assert len(block_calls) == 0  # only listed children fetch further pages
    root_calls = [r for r in seen if r.url.path == "/v1/blocks/p1/children"]
    assert len(root_calls) == 2


async def test_rate_limited_requests_wait_and_retry():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            search=[[_page_obj("p1", "One")]],
            rate_limit_first=1,
            seen=seen,
        )
    )
    assert [item.external_id for item in items] == ["p1"]
    me_calls = [r for r in seen if r.url.path == "/v1/users/me"]
    assert len(me_calls) == 2  # the 429, then the honoured retry


async def test_a_persistent_rate_limit_raises_an_honest_bounded_error():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(no.NotionAdapterError, match="rate limit"):
        await _collect(httpx.MockTransport(handler))
    assert len(seen) == no._MAX_ATTEMPTS  # bounded, never an endless retry loop


async def test_a_revoked_token_says_what_to_do_and_never_echoes_it():
    with pytest.raises(no.NotionAdapterError) as excinfo:
        await _collect(_transport(me_status=401))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "ntn-test" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    seen: list[httpx.Request] = []
    with pytest.raises(no.NotionAdapterError, match="not connected"):
        await _collect(_transport(seen=seen))
    assert seen == []  # refused before a single request left the machine


async def test_a_numeric_checkpoint_narrows_by_last_edited_time():
    moment = datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC)
    db = _db_obj("db-1", "Projects")
    stale = _page_obj("old-page", "Old", edited="2026-02-01T10:00:00.000Z")
    fresh = _page_obj("new-page", "Fresh", edited="2026-03-04T09:00:00.000Z")
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(search=[[db, stale, fresh]], seen=seen),
        checkpoint=str(int(moment.timestamp() * 1_000_000_000)),
    )
    # The previous UTC day, not the cursor's own day: a whole-day rewind is
    # what guarantees nothing is skipped at a boundary; overlaps upsert.
    query = next(r for r in seen if r.url.path == "/v1/databases/db-1/query")
    body = json.loads(query.content)
    assert body["filter"]["last_edited_time"] == {"on_or_after": "2026-03-03"}
    # The untouched page is skipped WITHOUT spending its block requests...
    assert [item.external_id for item in items] == ["new-page"]
    assert not any(r.url.path == "/v1/blocks/old-page/children" for r in seen)
    # ...while the touched page is re-yielded in full.
    assert any(r.url.path == "/v1/blocks/new-page/children" for r in seen)


async def test_a_backfill_checkpoint_is_read_as_no_cursor():
    """The bridge passes the last item's external_id during a backfill; that
    is not a cursor, and mis-parsing it would silently skip history."""
    db = _db_obj("db-1", "Projects")
    seen: list[httpx.Request] = []
    await _collect(_transport(search=[[db]], seen=seen), checkpoint="row-1")
    query = next(r for r in seen if r.url.path == "/v1/databases/db-1/query")
    assert "filter" not in json.loads(query.content)


async def test_an_oversized_item_is_capped_with_a_marker():
    page = _page_obj("page-1", "Huge")
    huge = _block("b1", "paragraph", "x" * 1_100_000)
    items = await _collect(_transport(search=[[page]], blocks={"page-1": [[huge]]}))
    body = items[0].body
    assert body.endswith("[truncated — this item exceeded the 1 MB import cap]")
    assert len(body.encode("utf-8")) < 1_100_000


async def test_media_blocks_become_markers_never_binaries():
    page = _page_obj("page-1", "Media")
    root = [
        _block("b1", "image", caption=_rt("architecture sketch")),
        _block("b2", "file", name="notes.pdf"),
        _block("b3", "bookmark", url="https://example.com/post"),
    ]
    items = await _collect(_transport(search=[[page]], blocks={"page-1": [root]}))
    body = items[0].body
    assert "[image: architecture sketch]" in body
    assert "[file: notes.pdf]" in body
    assert "https://example.com/post" in body


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(no.INTEGRATION_ID)
    assert spec is not None and spec.id == "notion"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(no.INTEGRATION_ID, no.notion_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:notion") is True
    finally:
        plugin_bridge.unregister_pull_adapter(no.INTEGRATION_ID)
