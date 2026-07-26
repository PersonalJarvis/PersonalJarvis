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
        # Search results carry this; the adapter reads the thread from it.
        "comments_url": "https://api.github.com/repos/acme/widgets/issues/7/comments",
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
    comments: list[dict[str, Any]] | None = None,
    repos: list[dict[str, Any]] | None = None,
    readme: str = "",
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, content=json.dumps({"login": login}).encode())
        if path == "/user/repos":
            page = int(request.url.params.get("page", "1"))
            rows = (repos or []) if page == 1 else []
            return httpx.Response(200, content=json.dumps(rows).encode())
        if path.endswith("/readme"):
            if not readme:
                return httpx.Response(404, content=b"{}")
            return httpx.Response(200, content=readme.encode())
        if path.endswith("/comments"):
            return httpx.Response(200, content=json.dumps(comments or []).encode())
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

# ---------------------------------------------------------------------------
# Comment threads — where the decision usually lives
# ---------------------------------------------------------------------------


async def test_the_discussion_is_stored_not_just_the_opening_post():
    """An opening post says what was PROPOSED; the thread says what was decided.

    Storing only the first message is how a memory ends up confidently
    reporting a plan that was argued out of existence three comments later.
    """
    thread = [
        {
            "user": {"login": "reviewer"},
            "created_at": "2026-03-02T09:00:00Z",
            "body": "This breaks the wake word on Linux.",
        },
        {
            "user": {"login": "rubenluetke10-beep"},
            "created_at": "2026-03-02T10:00:00Z",
            "body": "Good catch — switching to the energy gate instead.",
        },
    ]
    items = await _collect(
        transport=_transport([[_issue(comments=2)]], comments=thread)
    )
    body = items[0].body
    assert "--- discussion ---" in body
    assert "reviewer (2026-03-02): This breaks the wake word on Linux." in body
    assert "switching to the energy gate instead" in body
    assert items[0].metadata["comments_fetched"] is True


async def test_an_item_without_comments_costs_no_extra_request():
    seen: list[httpx.Request] = []
    await _collect(transport=_transport([[_issue(comments=0)]], seen=seen))
    assert not [r for r in seen if r.url.path.endswith("/comments")]


async def test_an_unreadable_thread_keeps_the_item_and_says_so():
    """A failed thread must never take its item down with it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, content=json.dumps({"login": "x"}).encode())
        if request.url.path.endswith("/comments"):
            return httpx.Response(500, content=b"{}")
        if request.url.path == "/user/repos":
            return httpx.Response(200, content=b"[]")
        return httpx.Response(
            200, content=json.dumps({"items": [_issue(comments=3)]}).encode()
        )

    items = await _collect(transport=httpx.MockTransport(handler))
    assert len(items) == 1
    # Falls back to the honest note rather than implying the post was all of it.
    assert "3 comment(s) on GitHub" in items[0].body
    assert items[0].metadata["comments_fetched"] is False


async def test_a_huge_thread_is_capped_and_declares_the_remainder():
    thread = [
        {"user": {"login": "u"}, "created_at": "2026-03-02T09:00:00Z", "body": f"c{i}"}
        for i in range(60)
    ]
    items = await _collect(
        transport=_transport([[_issue(comments=200)]], comments=thread)
    )
    body = items[0].body
    assert body.count("u (2026-03-02):") == 30
    assert "further comment(s) on GitHub" in body


# ---------------------------------------------------------------------------
# Repository profiles — "which project was that again?"
# ---------------------------------------------------------------------------


def _repo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 555,
        "full_name": "acme/widgets",
        "html_url": "https://github.com/acme/widgets",
        "description": "The widget pipeline.",
        "language": "Python",
        "topics": ["widgets", "pipeline"],
        "private": True,
        "owner": {"login": "acme"},
        "created_at": "2025-01-05T08:00:00Z",
        "pushed_at": "2026-03-09T12:00:00Z",
    }
    base.update(overrides)
    return base


async def test_a_repository_becomes_a_profile_not_a_code_dump():
    items = await _collect(
        transport=_transport(
            [[]], repos=[_repo()], readme="# Widgets\n\nRuns the widget pipeline."
        )
    )
    profile = next(i for i in items if i.metadata["kind"] == "repository")
    assert profile.external_id == "repo:555"
    assert profile.title == "acme/widgets"
    assert profile.permalink == "https://github.com/acme/widgets"
    # What it IS: visibility, language, topics, purpose, README.
    assert "acme/widgets · repository · private · Python" in profile.body
    assert "widgets, pipeline" in profile.body
    assert "The widget pipeline." in profile.body
    assert "Runs the widget pipeline." in profile.body
    # And explicitly NOT a file tree: the timestamp is the repo's creation,
    # the cursor rides on the last push.
    assert profile.timestamp_utc == "2025-01-05T08:00:00Z"
    assert profile.metadata["mtime_ns"] == gh._to_ns("2026-03-09T12:00:00Z")


async def test_a_repository_without_a_readme_is_still_a_profile():
    """404 on the README is a normal state, not a failure."""
    items = await _collect(transport=_transport([[]], repos=[_repo()], readme=""))
    profile = next(i for i in items if i.metadata["kind"] == "repository")
    assert "The widget pipeline." in profile.body


async def test_an_overlong_readme_is_truncated_with_a_pointer():
    items = await _collect(
        transport=_transport([[]], repos=[_repo()], readme="x" * 20000)
    )
    profile = next(i for i in items if i.metadata["kind"] == "repository")
    assert "README truncated" in profile.body
    assert len(profile.body) < 12000


async def test_a_failed_repository_walk_keeps_the_issues_already_read():
    """The issues are the valuable half; a repo-list failure must not void them."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, content=json.dumps({"login": "x"}).encode())
        if request.url.path == "/user/repos":
            return httpx.Response(500, content=b"{}")
        return httpx.Response(
            200, content=json.dumps({"items": [_issue()]}).encode()
        )

    items = await _collect(transport=httpx.MockTransport(handler))
    assert [i.metadata["kind"] for i in items] == ["issue"]


async def test_repositories_are_read_after_the_issues():
    """A rate-limit stop should cost the cheap half, not the discussions."""
    seen: list[httpx.Request] = []
    await _collect(
        transport=_transport([[_issue()]], repos=[_repo()], seen=seen, readme="hi")
    )
    paths = [r.url.path for r in seen]
    assert paths.index("/search/issues") < paths.index("/user/repos")
