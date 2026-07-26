"""The ClickUp pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: stable
per-task ids (a re-run must upsert, not duplicate), a real deep link on every
item, closed tasks and subtasks explicitly asked for, comments walked through
ClickUp's own pagination and folded chronologically, deterministic
oldest-to-newest streaming for the backfill checkpoint, bounded 429 handling,
and failure messages a user can act on that never carry the token.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import clickup as cu
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected ClickUp plugin, without touching the host's keyring."""

    class _Tokens:
        access = "clickup-test-token"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-clickup",
        config={"integration_id": "plugin:clickup"},
        secret_get=lambda _name: None,
    )


def _ms(iso: str) -> str:
    moment = datetime.fromisoformat(iso).replace(tzinfo=UTC)
    return str(int(moment.timestamp() * 1000))


def _task(task_id: str = "abc1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": task_id,
        "name": "Ship the wake-word fix",
        "description": "The wake word misses every third call.",
        "status": {"status": "in progress"},
        "archived": False,
        "date_created": _ms("2026-03-01T10:00:00"),
        "date_updated": _ms("2026-03-04T12:00:00"),
        "due_date": _ms("2026-04-01T00:00:00"),
        "creator": {"username": "Bob"},
        "assignees": [{"username": "Alice"}],
        "list": {"id": "L1", "name": "Widgets"},
        "folder": {"id": "F1", "name": "Engineering", "hidden": False},
        "url": f"https://app.clickup.com/t/{task_id}",
    }
    base.update(overrides)
    return base


def _comment(iso: str, text: str, author: str = "Alice", comment_id: str = "") -> dict[str, Any]:
    return {
        "id": comment_id or f"c-{iso}",
        "comment_text": text,
        "user": {"username": author},
        "date": _ms(iso),
    }


def _json(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def _transport(
    *,
    teams: list[dict[str, Any]] | None = None,
    task_pages: dict[str, list[list[dict[str, Any]]]] | None = None,
    comment_pages: dict[str, list[list[dict[str, Any]]]] | None = None,
    rate_limit_first: int = 0,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake ClickUp account: teams, the team-task walk, per-task comments."""
    remaining_429 = {"n": rate_limit_first}
    comment_calls: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if remaining_429["n"] > 0:
            remaining_429["n"] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        path = request.url.path
        params = request.url.params
        if path == "/api/v2/team":
            return _json({"teams": teams or [{"id": "9001", "name": "Acme"}]})
        if path.startswith("/api/v2/team/") and path.endswith("/task"):
            team_id = path.split("/")[-2]
            pages = (task_pages or {}).get(team_id, [[]])
            page = int(params.get("page") or 0)
            return _json({"tasks": pages[page] if page < len(pages) else []})
        if path.startswith("/api/v2/task/") and path.endswith("/comment"):
            task_id = path.split("/")[-2]
            pages = (comment_pages or {}).get(task_id, [[]])
            index = comment_calls.get(task_id, 0)
            comment_calls[task_id] = index + 1
            return _json({"comments": pages[index] if index < len(pages) else []})
        return httpx.Response(404, content=b"{}")

    return httpx.MockTransport(handler)


async def _collect(
    transport: httpx.MockTransport, checkpoint: str | None = None
) -> list:
    return [
        item
        async for item in cu.clickup_pull_adapter(_ctx(), checkpoint, transport=transport)
    ]


async def test_a_task_becomes_a_linkable_dated_item():
    comments = [
        [
            _comment("2026-03-02T09:00:00", "On it"),
            _comment("2026-03-01T11:00:00", "Can you take this?", author="Bob"),
        ]
    ]
    items = await _collect(
        _transport(task_pages={"9001": [[_task()]]}, comment_pages={"abc1": comments})
    )
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == "task:abc1"
    # Evidence has to lead back to where it lives — mandatory from item one.
    assert item.permalink == "https://app.clickup.com/t/abc1"
    # WHEN IT HAPPENED, not when it was last touched.
    assert item.timestamp_utc == "2026-03-01T10:00:00Z"
    assert item.title == "Ship the wake-word fix"
    assert item.author_raw == "Bob"
    assert item.thread_key == "L1"
    # The composed header keeps the context a bare description would lose.
    assert "Engineering / Widgets · task · in progress" in item.body
    assert "assigned to Alice" in item.body
    assert "due 2026-04-01T00:00:00Z" in item.body
    assert "created by Bob" in item.body
    assert "The wake word misses every third call." in item.body
    # Comments fold in chronologically, oldest first.
    assert item.body.index("Can you take this?") < item.body.index("On it")
    # The cursor rides on the key the sync runner advances.
    assert item.metadata["mtime_ns"] == cu._ms_to_ns(_ms("2026-03-04T12:00:00"))
    assert item.metadata["comments"] == 2


async def test_closed_and_subtask_rows_are_explicitly_asked_for():
    seen: list[httpx.Request] = []
    closed = _task(archived=True, status={"status": "complete"})
    items = await _collect(_transport(task_pages={"9001": [[closed]]}, seen=seen))
    assert items[0].metadata["state"] == "complete"
    assert items[0].metadata["archived"] is True
    assert "archived" in items[0].body
    walk = next(r for r in seen if r.url.path == "/api/v2/team/9001/task")
    # Without these flags ClickUp silently omits closed tasks and subtasks.
    assert walk.url.params.get("include_closed") == "true"
    assert walk.url.params.get("subtasks") == "true"


async def test_a_hidden_folder_never_names_the_plumbing():
    """ClickUp models folderless lists as a "hidden" folder; its API name
    must not leak into the record."""
    folderless = _task(folder={"id": "F0", "name": "hidden", "hidden": True})
    items = await _collect(_transport(task_pages={"9001": [[folderless]]}))
    assert "hidden /" not in items[0].body
    assert "Widgets · task" in items[0].body


async def test_a_numeric_checkpoint_narrows_the_walk_by_update_time():
    seen: list[httpx.Request] = []
    moment = datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC)
    await _collect(
        _transport(seen=seen),
        checkpoint=str(int(moment.timestamp() * 1_000_000_000)),
    )
    walk = next(r for r in seen if r.url.path == "/api/v2/team/9001/task")
    # Five minutes before the cursor, in the epoch milliseconds the API
    # speaks: the overlap is what makes a boundary safe.
    expected = int(moment.timestamp() * 1000) - 300_000
    assert walk.url.params.get("date_updated_gt") == str(expected)


async def test_a_backfill_checkpoint_resumes_strictly_after_it():
    first = _task("aaa", date_created=_ms("2026-03-01T10:00:00"))
    second = _task("bbb", date_created=_ms("2026-03-02T10:00:00"))
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(task_pages={"9001": [[first, second]]}, seen=seen),
        checkpoint="task:aaa",
    )
    assert [item.external_id for item in items] == ["task:bbb"]
    # The skipped task's comments are never fetched — resume must not
    # re-spend the budget on rows the store already holds.
    comment_calls = [r for r in seen if r.url.path.endswith("/comment")]
    assert [r.url.path for r in comment_calls] == ["/api/v2/task/bbb/comment"]


async def test_an_unknown_checkpoint_shape_is_read_as_no_cursor():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(task_pages={"9001": [[_task()]]}, seen=seen),
        checkpoint="issue:4242",
    )
    assert len(items) == 1
    walk = next(r for r in seen if r.url.path == "/api/v2/team/9001/task")
    assert walk.url.params.get("date_updated_gt") is None


async def test_task_pages_are_walked_to_the_end():
    pages = [
        [_task(f"t{i:03d}", date_created=_ms("2026-03-01T10:00:00")) for i in range(100)],
        [_task("t999", date_created=_ms("2026-03-02T10:00:00"))],
    ]
    seen: list[httpx.Request] = []
    items = await _collect(_transport(task_pages={"9001": pages}, seen=seen))
    assert len(items) == 101
    walk = [r for r in seen if r.url.path == "/api/v2/team/9001/task"]
    assert len(walk) == 2  # stopped after the short second page


async def test_every_workspace_the_token_reaches_is_walked():
    teams = [{"id": "9002", "name": "Beta"}, {"id": "9001", "name": "Acme"}]
    items = await _collect(
        _transport(
            teams=teams,
            task_pages={
                "9001": [[_task("aaa", date_created=_ms("2026-03-01T10:00:00"))]],
                "9002": [[_task("bbb", date_created=_ms("2026-02-01T10:00:00"))]],
            },
        )
    )
    # Both workspaces contribute, merged into ONE oldest-to-newest stream.
    assert [item.external_id for item in items] == ["task:bbb", "task:aaa"]


async def test_comment_pages_are_walked_through_the_pagination():
    page_one = [
        _comment("2026-03-02T10:00:00", f"comment {i}", comment_id=f"c{i}")
        for i in range(cu._COMMENT_PAGE)
    ]
    page_two = [_comment("2026-03-01T09:00:00", "the very first comment", comment_id="c-old")]
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            task_pages={"9001": [[_task()]]},
            comment_pages={"abc1": [page_one, page_two]},
            seen=seen,
        )
    )
    body = items[0].body
    # The older page arrived AND sorts before the newer one.
    assert body.index("the very first comment") < body.index("comment 0")
    assert items[0].metadata["comments"] == cu._COMMENT_PAGE + 1
    comment_calls = [r for r in seen if r.url.path.endswith("/comment")]
    assert len(comment_calls) == 2
    # The second ask carries ClickUp's start/start_id pagination pair.
    assert comment_calls[1].url.params.get("start_id") == page_one[-1]["id"]


async def test_the_bare_token_convention_is_kept():
    """ClickUp rejects "Bearer "-prefixed personal tokens; the header carries
    the token bare, exactly as its docs demand."""
    seen: list[httpx.Request] = []
    await _collect(_transport(seen=seen))
    assert seen[0].headers["Authorization"] == "clickup-test-token"


async def test_rate_limited_requests_wait_and_retry():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(task_pages={"9001": [[_task()]]}, rate_limit_first=1, seen=seen)
    )
    assert len(items) == 1
    assert seen[0].url.path == seen[1].url.path == "/api/v2/team"


async def test_a_persistent_rate_limit_raises_an_honest_bounded_error():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(cu.ClickUpAdapterError, match="rate limit"):
        await _collect(httpx.MockTransport(handler))
    assert len(seen) == cu._MAX_ATTEMPTS  # bounded, never an endless retry loop


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"err":"Token invalid","ECODE":"OAUTH_025"}')

    with pytest.raises(cu.ClickUpAdapterError) as excinfo:
        await _collect(httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "clickup-test-token" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    seen: list[httpx.Request] = []
    with pytest.raises(cu.ClickUpAdapterError, match="not connected"):
        await _collect(_transport(seen=seen))
    assert seen == []  # refused before a single request left the machine


async def test_an_oversized_item_is_capped_with_a_marker():
    huge = _task(description="x" * 1_100_000)
    items = await _collect(_transport(task_pages={"9001": [[huge]]}))
    body = items[0].body
    assert body.endswith("[truncated — the full text lives at the link above]")
    assert len(body) < 1_100_000


async def test_a_row_without_a_permalink_is_skipped_not_faked():
    """Every item must deep-link back; a row that cannot is dropped."""
    items = await _collect(
        _transport(task_pages={"9001": [[_task(url=""), _task("keep")]]})
    )
    assert [item.external_id for item in items] == ["task:keep"]


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(cu.INTEGRATION_ID)
    assert spec is not None and spec.id == "clickup"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(cu.INTEGRATION_ID, cu.clickup_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:clickup") is True
    finally:
        plugin_bridge.unregister_pull_adapter(cu.INTEGRATION_ID)
