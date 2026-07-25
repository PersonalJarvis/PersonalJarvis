"""The GitHub pull adapter, driven fully offline through MockTransport.

The first real reader behind the plugin bridge, so these tests pin what makes
a reader trustworthy rather than merely functional: a stable id (a re-run must
upsert, not duplicate), a real deep link on every item, the event's own time
rather than its last edit, and failure messages a user can act on that never
carry the token.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import github as gh
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected GitHub plugin, without touching the host's keyring."""

    class _Tokens:
        access = "gho_test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-github",
        config={"integration_id": "plugin:github"},
        secret_get=lambda _name: None,
    )


def _issue(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 4242,
        "number": 7,
        "title": "Ship the wake-word fix",
        "html_url": "https://github.com/acme/widgets/issues/7",
        "repository_url": "https://api.github.com/repos/acme/widgets",
        "state": "open",
        "body": "The wake word misses every third call.",
        "user": {"login": "rubenluetke10-beep"},
        "labels": [{"name": "bug"}],
        "comments": 0,
        "created_at": "2026-03-01T10:00:00Z",
        "updated_at": "2026-03-04T12:00:00Z",
    }
    base.update(overrides)
    return base


def _transport(
    pages: list[list[dict[str, Any]]],
    *,
    login: str = "rubenluetke10-beep",
    headers: dict[str, str] | None = None,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/user":
            return httpx.Response(200, content=json.dumps({"login": login}).encode())
        page = int(request.url.params.get("page", "1"))
        rows = pages[page - 1] if page <= len(pages) else []
        return httpx.Response(
            200,
            content=json.dumps({"items": rows}).encode(),
            headers=headers or {},
        )

    return httpx.MockTransport(handler)


async def _collect(checkpoint: str | None = None, **kwargs: Any) -> list:
    return [
        item
        async for item in gh.github_pull_adapter(_ctx(), checkpoint, **kwargs)
    ]


async def test_an_issue_becomes_a_linkable_dated_item():
    items = await _collect(transport=_transport([[_issue()]]))
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == "issue:4242"
    # Evidence has to lead back to where it lives — mandatory from item one.
    assert item.permalink == "https://github.com/acme/widgets/issues/7"
    # WHEN IT HAPPENED, not when it was last touched.
    assert item.timestamp_utc == "2026-03-01T10:00:00Z"
    assert item.title == "Ship the wake-word fix"
    assert item.author_raw == "rubenluetke10-beep"
    assert item.thread_key == "acme/widgets#7"
    # The composed header keeps the context a bare body would lose.
    assert "acme/widgets · issue #7 · open" in item.body
    assert "bug" in item.body
    assert "The wake word misses every third call." in item.body


async def test_a_merged_pull_request_is_not_recorded_as_merely_closed():
    """"Closed" and "merged" are opposite outcomes; conflating them loses the
    only fact a later question usually turns on."""
    merged = _issue(
        id=99,
        number=12,
        state="closed",
        pull_request={"merged_at": "2026-03-05T09:00:00Z"},
    )
    rejected = _issue(id=100, number=13, state="closed", pull_request={"merged_at": None})
    items = await _collect(transport=_transport([[merged, rejected]]))
    assert items[0].metadata["state"] == "merged"
    assert items[0].metadata["kind"] == "pull_request"
    assert items[1].metadata["state"] == "closed without merging"


async def test_unfetched_comments_are_declared_rather_than_silently_dropped():
    """One request per issue would blow the 30/min search budget, so the
    threads are not pulled — the record must say so instead of implying the
    body is the whole conversation."""
    items = await _collect(transport=_transport([[_issue(comments=5)]]))
    assert "5 comment(s) on GitHub" in items[0].body
    assert items[0].metadata["comments"] == 5


async def test_the_cursor_rides_on_the_key_the_sync_runner_advances():
    items = await _collect(transport=_transport([[_issue()]]))
    # 2026-03-04T12:00:00Z in nanoseconds — the runner reads exactly this key.
    assert items[0].metadata["mtime_ns"] == gh._to_ns("2026-03-04T12:00:00Z")
    assert items[0].metadata["mtime_ns"] > 0


async def test_a_numeric_checkpoint_narrows_the_query_by_date():
    seen: list[httpx.Request] = []
    await _collect(
        checkpoint=str(gh._to_ns("2026-03-04T12:00:00Z")),
        transport=_transport([[]], seen=seen),
    )
    search = next(r for r in seen if r.url.path == "/search/issues")
    query = search.url.params["q"]
    assert "involves:rubenluetke10-beep" in query
    # A day earlier than the cursor: GitHub's updated:>= works on whole days,
    # so re-asking from the previous day is what makes a boundary safe.
    assert "updated:>=2026-03-03" in query


async def test_a_backfill_checkpoint_is_read_as_no_cursor():
    """The bridge passes the last item's external_id during a backfill.

    That is not a date, and mis-parsing it would silently narrow the query to
    nothing. It must degrade to a full read.
    """
    seen: list[httpx.Request] = []
    await _collect(checkpoint="issue:4242", transport=_transport([[]], seen=seen))
    search = next(r for r in seen if r.url.path == "/search/issues")
    assert "updated:" not in search.url.params["q"]


async def test_pagination_stops_on_a_short_page():
    pages = [[_issue(id=i, number=i) for i in range(100)], [_issue(id=999, number=999)]]
    seen: list[httpx.Request] = []
    items = await _collect(transport=_transport(pages, seen=seen))
    assert len(items) == 101
    searches = [r for r in seen if r.url.path == "/search/issues"]
    assert len(searches) == 2  # stopped after the short second page


async def test_a_nearly_spent_rate_budget_stops_early_instead_of_failing():
    """The cursor is already checkpointed, so stopping is free; running into
    the wall mid-page is not."""
    pages = [[_issue(id=i, number=i) for i in range(100)] for _ in range(3)]
    seen: list[httpx.Request] = []
    items = await _collect(
        transport=_transport(pages, headers={"x-ratelimit-remaining": "1"}, seen=seen)
    )
    assert len(items) == 100
    assert len([r for r in seen if r.url.path == "/search/issues"]) == 1


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"message":"Bad credentials"}')

    with pytest.raises(gh.GitHubAdapterError) as excinfo:
        await _collect(transport=httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "gho_test" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(gh.GitHubAdapterError, match="not connected"):
        await _collect(transport=_transport([[]]))


async def test_an_expired_connection_is_named_as_such(monkeypatch):
    class _Tokens:
        access = "gho_old"
        needs_reauth = True

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(gh.GitHubAdapterError, match="expired"):
        await _collect(transport=_transport([[]]))


async def test_a_row_without_a_permalink_is_skipped_not_faked():
    """Every item must deep-link back; a row that cannot is dropped."""
    items = await _collect(
        transport=_transport([[_issue(html_url=""), _issue(id=5, number=8)]])
    )
    assert [i.external_id for i in items] == ["issue:5"]


def test_the_adapter_registers_under_the_bridge_candidate_id():
    from jarvis.ultrawiki import adapters
    from jarvis.ultrawiki.connectors import plugin_bridge

    adapters.reset_for_tests()
    plugin_bridge.unregister_pull_adapter(gh.INTEGRATION_ID)
    assert plugin_bridge.has_pull_adapter(gh.INTEGRATION_ID) is False

    registered = adapters.register_builtin_adapters()
    assert gh.INTEGRATION_ID in registered
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    assert plugin_bridge.has_pull_adapter("plugin:github") is True
